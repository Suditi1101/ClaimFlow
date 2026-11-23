"""Authenticated customer chat; bounded server-side session history."""

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from claims.models import Claim
from policies.models import Policy
from verification.chat import answer_question
from verification.chat_validation import CHAT_VALIDATION_REASONS
from verification.rag_clients import RagError


ERROR_MESSAGES = {
    "rag_disabled": "Chat is not enabled yet. Contact your insurer for assistance.",
    "provider_rate_limited": "The AI service has reached its rate limit. Please wait a minute before trying again.",
    "provider_auth_failed": "Chat configuration needs attention. Please contact your insurer.",
    "groq_not_configured": "Chat configuration needs attention. Please contact your insurer.",
    "gemini_not_configured": "Policy search is not configured yet. Please contact your insurer.",
    "invalid_chat_answer": "The answer could not be validated against your sources. Please rephrase your question or contact your insurer.",
}


class ChatForm(forms.Form):
    context = forms.ChoiceField(label="Discuss a claim or policy")
    question = forms.CharField(
        max_length=1500,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Ask about your claim status, rejection reason, required documents, or coverage…",
            }
        ),
    )

    def __init__(self, *args, customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.claims = {
            "claim:" + str(item.pk): item
            for item in Claim.objects.filter(
                customer=customer, policy__customer=customer
            )
            .select_related("policy")
            .order_by("-created_at")[:100]
        }
        self.policies = {
            "policy:" + str(item.pk): item
            for item in Policy.objects.filter(customer=customer).order_by("-starts_on")[
                :100
            ]
        }
        self.fields["context"].choices = (
            [("", "Choose a claim or policy")]
            + [
                (key, f"Claim {item.pk} · {item.get_status_display()}")
                for key, item in self.claims.items()
            ]
            + [(key, f"Policy {item.reference}") for key, item in self.policies.items()]
        )


@login_required
@csrf_protect
@require_http_methods(["GET", "POST"])
def chat(request):
    selected = (
        request.POST.get("context", "")
        if request.method == "POST"
        else request.GET.get("context", "")
    )
    form = ChatForm(
        request.POST if request.method == "POST" else None,
        customer=request.user,
        initial={"context": selected},
    )
    allowed = {**form.claims, **form.policies}
    if selected and selected not in allowed:
        return HttpResponse("Claim or policy not found.", status=404)
    session_key = "customer_chat_" + str(request.user.pk)
    conversations = request.session.get(session_key, {})
    history = conversations.get(selected, [])
    error = ""
    status = 200
    if request.method == "POST":
        if request.POST.get("action") == "clear" and selected in allowed:
            conversations.pop(selected, None)
            request.session[session_key] = conversations
            return redirect(reverse("customer-chat") + "?context=" + selected)
        if form.is_valid():
            # Per-user quota plus a short lock prevents double-clicks and parallel
            # requests from spending API quota or losing a conversation turn.
            key = f"customer-chat-rate:{request.user.pk}"
            if cache.add(key, 1, 300):
                count = 1
            else:
                try:
                    count = cache.incr(key)
                except ValueError:
                    cache.set(key, 1, 300)
                    count = 1
            lock = f"customer-chat-busy:{request.user.pk}"
            if count > 10:
                error, status = (
                    "You have sent several questions. Please try again in five minutes.",
                    429,
                )
            elif not cache.add(lock, True, 90):
                error, status = (
                    "Another answer is being prepared. Please wait before sending again.",
                    429,
                )
            else:
                try:
                    turn = answer_question(
                        customer=request.user,
                        question=form.cleaned_data["question"],
                        history=history,
                        claim=form.claims.get(selected),
                        policy=form.policies.get(selected),
                    )
                    conversations[selected] = (history + [turn])[-6:]
                    while len(conversations) > 5:
                        conversations.pop(next(iter(conversations)))
                    request.session[session_key] = conversations
                    return redirect(reverse("customer-chat") + "?context=" + selected)
                except RagError as exc:
                    error = ERROR_MESSAGES.get(
                        str(exc),
                        "Chat is temporarily unavailable. Please try again later or contact your insurer.",
                    )
                    if str(exc) == "invalid_chat_answer":
                        error = (
                            CHAT_VALIDATION_REASONS.get(exc.detail, error)
                            + " Your question has been kept; please try again or contact your insurer."
                        )
                    status = 503
                finally:
                    cache.delete(lock)
    response = render(
        request,
        "portal/chat.html",
        {
            "form": form,
            "history": history,
            "selected": selected,
            "chat_error": error,
            "has_contexts": bool(allowed),
        },
        status=status,
    )
    response["Cache-Control"] = "no-store, private"
    return response
