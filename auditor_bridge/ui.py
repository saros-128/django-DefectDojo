"""Small session-authenticated operator form; API validation and services are shared."""
import json

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.serializers.json import DjangoJSONEncoder
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from auditor_bridge import services
from auditor_bridge.config import BridgeError
from auditor_bridge.models import Profile
from auditor_bridge.serializers import ProfileSerializer


class ProfileForm(forms.Form):
    key = forms.SlugField(max_length=100)
    desired = forms.JSONField(required=False, widget=forms.Textarea(attrs={"rows": 24, "cols": 100}))
    action = forms.ChoiceField(choices=[("save", "Save"), ("sync", "Sync"), ("disable", "Disable")], widget=forms.HiddenInput)


@login_required
@csrf_protect
@require_http_methods(["GET", "POST"])
def profiles(request):
    if not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied
    selected = None
    if request.method == "POST":
        form = ProfileForm(request.POST)
        if form.is_valid():
            key, action = form.cleaned_data["key"], form.cleaned_data["action"]
            if action == "save":
                serializer = ProfileSerializer(data=form.cleaned_data["desired"])
                if serializer.is_valid():
                    desired = json.loads(json.dumps(serializer.validated_data, cls=DjangoJSONEncoder))
                    try:
                        selected = services.save_profile(key, desired)
                    except BridgeError as exc:
                        form.add_error(None, str(exc))
                else:
                    form.add_error("desired", json.dumps(serializer.errors))
            else:
                selected = get_object_or_404(Profile, key=key)
                if selected.state == "deleted":
                    form.add_error(None, "profile_deleted")
                elif action == "disable":
                    try:
                        selected = services.disable_profile(key)
                    except BridgeError as exc:
                        form.add_error(None, str(exc))
                else:
                    selected = services.synchronize_profile(key)
    else:
        key = request.GET.get("key", "")
        if key:
            selected = get_object_or_404(Profile, key=key)
        form = ProfileForm(initial={"key": key, "desired": selected.desired if selected else {}, "action": "save"})
    return render(request, "auditor_bridge/profiles.html", {
        "form": form, "profiles": Profile.objects.order_by("key")[:200], "selected": selected,
        "readback": json.dumps(selected.remote, indent=2) if selected else "",
    })
