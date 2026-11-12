from django import forms
from django.conf import settings
from policies.documents import validate_version


class PolicyDocumentForm(forms.Form):
    title = forms.CharField(max_length=200)
    version = forms.CharField(
        max_length=60,
        help_text="For example: v1. Published versions cannot be overwritten.",
    )
    effective_from = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    effective_to = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    file = forms.FileField(
        label="Policy wording PDF",
        widget=forms.ClearableFileInput(attrs={"accept": "application/pdf"}),
    )

    def __init__(self, *args, policy, **kwargs):
        self.policy = policy
        super().__init__(*args, **kwargs)
        self.fields[
            "file"
        ].help_text = f"PDF only; maximum {settings.MAX_DOCUMENT_BYTES // (1024 * 1024)} MiB. Scanned pages require manual OCR."

    def clean_file(self):
        file = self.cleaned_data["file"]
        if file.size > settings.MAX_DOCUMENT_BYTES or file.read(5) != b"%PDF-":
            raise forms.ValidationError("Upload a PDF within the upload size limit.")
        file.seek(0)
        return file

    def clean(self):
        data = super().clean()
        if all(key in data for key in ("version", "effective_from", "effective_to")):
            validate_version(
                self.policy,
                data["version"],
                data["effective_from"],
                data["effective_to"],
            )
        return data
