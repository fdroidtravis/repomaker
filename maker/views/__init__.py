import pathlib

from allauth.account.forms import LoginForm, SignupForm, ResetPasswordForm
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.forms import TextInput, ModelForm
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils.translation import ugettext_lazy as _
from django.views.static import serve

from maker import DEFAULT_USER_NAME
from maker.models import RemoteRepository, Repository
from maker.storage import USER_RE, REMOTE_REPO_RE


# TODO remove when not needed anymore for testing
def update(request, repo_id):
    repo = get_object_or_404(Repository, pk=repo_id)
    if repo.user != request.user:
        return HttpResponseForbidden()

    repo.update_async()
    return HttpResponse("Updated")


# TODO remove when not needed anymore for testing
def publish(request, repo_id):
    repo = get_object_or_404(Repository, pk=repo_id)
    if repo.user != request.user:
        return HttpResponseForbidden()

    repo.publish()
    return HttpResponse("Published")


# TODO remove when not needed anymore for testing
def remote_update(request, remote_repo_id):
    remote_repo = get_object_or_404(RemoteRepository, pk=remote_repo_id)
    if request.user not in remote_repo.users.all():
        return HttpResponseForbidden()

    remote_repo.update_index()
    return HttpResponse("Remote Repo Updated")


def media_serve(request, path, document_root=None, show_indexes=False):
    """
    A wrapper around django.views.static.serve with added authentication checks.
    """
    if not request.user.is_authenticated:
        return HttpResponseForbidden()  # only authenticated access to media

    path_parts = pathlib.Path(path).parts
    if len(path_parts) == 0:
        return HttpResponseForbidden()  # don't allow access to the root folder

    user_id = request.user.pk
    path_start = path_parts[0]
    if user_media_access(user_id, path_start) and \
            remote_repo_media_access(user_id, path_start):
        return serve(request, path, document_root, show_indexes)  # finally serve media file
    else:
        return HttpResponseForbidden()  # did not pass access tests


def user_media_access(user_id, path):
    """
    Returns true if the user with the given user_id is allowed to access this path
    or if this path does not point to a user's media files.
    Returns false otherwise.
    """
    match = USER_RE.match(path)
    if not match:
        return True  # not a path to user media
    if int(match.group(1)) == user_id:
        return True  # user is allowed to access
    return False  # user is not allowed to access


def remote_repo_media_access(user_id, path):
    """
    Returns true if the user with the given user_id is allowed to access this path
    or if this path does not point to a remote repository's media files.
    Returns false otherwise.
    """
    match = REMOTE_REPO_RE.match(path)
    if not match:
        return True  # not a path to a remote repo's media
    repo = get_object_or_404(RemoteRepository, pk=int(match.group(1)))
    if repo.pre_installed or repo.users.filter(pk=user_id).exists:
        return True  # repo is pre-installed or user added it
    return False  # user is not allowed to access this repo's files


class RmLoginForm(LoginForm):

    def __init__(self, *args, **kwargs):
        super(RmLoginForm, self).__init__(*args, **kwargs)
        # remove placeholders from form widgets
        fields = ['login', 'password']
        for field in fields:
            if 'placeholder' in self.fields[field].widget.attrs:
                del self.fields[field].widget.attrs['placeholder']


class RmResetPasswordForm(ResetPasswordForm):

    def __init__(self, *args, **kwargs):
        super(RmResetPasswordForm, self).__init__(*args, **kwargs)
        # Replace email placeholder
        self.fields['email'].label = _('Enter email')
        self.fields['email'].help_text = \
            _("Enter your email address and we'll send a link to reset it. "
              "If you did not sign up with an email, "
              "we cannot help you securely reset your password.")
        if 'placeholder' in self.fields['email'].widget.attrs:
            del self.fields['email'].widget.attrs['placeholder']


class RmSignupForm(SignupForm):

    def __init__(self, *args, **kwargs):
        super(RmSignupForm, self).__init__(*args, **kwargs)
        # remove placeholders from form widgets
        fields = ['username', 'email', 'password1', 'password2']
        for field in fields:
            if 'placeholder' in self.fields[field].widget.attrs:
                del self.fields[field].widget.attrs['placeholder']


class BaseModelForm(ModelForm):

    pass


class DataListTextInput(TextInput):

    def __init__(self, data_list, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._list = data_list

    def render(self, name, value, attrs=None, renderer=None):
        self.attrs.update({'list': 'list__%s' % name})
        text_html = super().render(name, value, attrs, renderer)
        data_list = '<datalist id="list__%s">' % name
        for item in self._list:
            data_list += '<option value="%s">%s</option>' % (item[0], item[1])
        data_list += '</datalist>'
        return text_html + data_list


class LoginOrSingleUserRequiredMixin(LoginRequiredMixin):
    """
    Mixin that should be used for all class-based views within this project.
    It verifies that the current user is authenticated
    or logs in the default user in single-user mode.
    """
    request = None
    kwargs = None

    def dispatch(self, request, *args, **kwargs):
        if settings.SINGLE_USER_MODE and not request.user.is_authenticated:
            user = User.objects.get(username=DEFAULT_USER_NAME)
            login(request, user)
        return super().dispatch(request, *args, **kwargs)
