from django.contrib import admin
from common.models import Outbox, DeadLetter


@admin.register(Outbox, DeadLetter)
class OperationsAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, r, obj=None):
        return tuple(f.name for f in self.model._meta.fields)

    def has_add_permission(self, r):
        return False

    def has_delete_permission(self, r, obj=None):
        return False
