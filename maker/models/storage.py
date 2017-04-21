import logging
import os
import re
from itertools import chain

from django.core.validators import RegexValidator, ValidationError, slug_re, force_text
from django.db import models
from django.conf import settings
from django.urls import reverse_lazy
from django.utils.deconstruct import deconstructible
from django.utils.translation import ugettext_lazy as _
from fdroidserver import server
from libcloud.storage.types import Provider

from maker.storage import get_identity_file_path, PrivateStorage, REPO_DIR, get_repo_root_path
from .repository import Repository

UL = '\u00a1-\uffff'  # unicode letters range (must be a unicode string, not a raw string)


class AbstractStorage(models.Model):
    repo = models.ForeignKey(Repository, on_delete=models.CASCADE)

    @staticmethod
    def get_name():
        raise NotImplementedError()

    def get_url(self):
        raise NotImplementedError()

    def get_repo_url(self):
        raise NotImplementedError()

    def get_edit_url(self):
        return reverse_lazy(self.edit_url_name, kwargs={'repo_id': self.repo.pk, 'pk': self.pk})

    def get_delete_url(self):
        return reverse_lazy(self.delete_url_name, kwargs={'repo_id': self.repo.pk, 'pk': self.pk})

    def publish(self):
        raise NotImplementedError()

    class Meta:
        abstract = True


class S3Storage(AbstractStorage):
    REGION_CHOICES = (
        (Provider.S3, _('US Standard')),
    )
    region = models.CharField(max_length=32, choices=REGION_CHOICES, default=Provider.S3)
    bucket = models.CharField(max_length=128)
    accesskeyid = models.CharField(max_length=128)
    secretkey = models.CharField(max_length=255)
    edit_url_name = 'storage_s3_update'
    delete_url_name = 'storage_s3_delete'

    def __str__(self):
        return 's3://' + str(self.bucket)

    @staticmethod
    def get_name():
        return _('Amazon S3 Storage')

    def get_url(self):
        # This needs to be changed when more region choices are added
        return "https://s3.amazonaws.com/" + str(self.bucket)

    def get_repo_url(self):
        return self.get_url() + "/fdroid/" + REPO_DIR

    def publish(self):
        logging.info("Publishing '%s' to %s", self.repo, self)
        config = self.repo.get_config()
        config['awsbucket'] = self.bucket
        config['awsaccesskeyid'] = self.accesskeyid
        config['awssecretkey'] = self.secretkey
        server.update_awsbucket(REPO_DIR)


@deconstructible
class UsernameValidator(RegexValidator):
    regex = slug_re
    message = _("Enter a valid user name consisting of letters, numbers, underscores or hyphens.")


@deconstructible
class HostnameValidator(RegexValidator):
    # IP patterns
    ipv4_re = r'(?:25[0-5]|2[0-4]\d|[0-1]?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|[0-1]?\d?\d)){3}'
    ipv6_re = r'\[[0-9a-f:\.]+\]'  # (simple regex, validated later)

    # Host patterns
    hostname_re = r'[a-z' + UL + r'0-9](?:[a-z' + UL + r'0-9-]{0,61}[a-z' + UL + r'0-9])?'
    # Max length for domain name labels is 63 characters per RFC 1034 sec. 3.1
    domain_re = r'(?:\.(?!-)[a-z' + UL + r'0-9-]{1,63}(?<!-))*'
    tld_re = (
        r'\.'  # dot
        r'(?!-)'  # can't start with a dash
        r'(?:[a-z' + UL + '-]{2,63}'  # domain label
                          r'|xn--[a-z0-9]{1,59})'  # or punycode label
                          r'(?<!-)'  # can't end with a dash
                          r'\.?'  # may have a trailing dot
    )
    host_re = '(' + hostname_re + domain_re + tld_re + '|localhost)'

    regex = re.compile(r'(?:' + ipv4_re + r'|' + ipv6_re + r'|' + host_re + r')\Z', re.IGNORECASE)
    message = _('Enter a valid hostname.')

    def __call__(self, value):
        value = force_text(value)
        # The maximum length of a full host name is 253 characters per RFC 1034
        # section 3.1. It's defined to be 255 bytes or less, but this includes
        # one byte for the length of the name and one byte for the trailing dot
        # that's used to indicate absolute names in DNS.
        if len(value) > 253:
            raise ValidationError(self.message, code=self.code)

        super(HostnameValidator, self).__call__(value)


@deconstructible
class PathValidator(RegexValidator):
    # FIXME this is probably too strict
    regex = re.compile(r'^(/[a-z' + UL + r'0-9-.]+)+?/?$', re.IGNORECASE)
    message = _('Enter a valid path.')


class AbstractSshStorage(AbstractStorage):
    host = models.CharField(max_length=256, validators=[HostnameValidator()])
    path = models.CharField(max_length=512, validators=[PathValidator()])
    identity_file = models.FileField(upload_to=get_identity_file_path, storage=PrivateStorage(),
                                     blank=True)
    url = models.URLField(max_length=2048)

    def __str__(self):
        return self.get_remote_url()

    def get_remote_url(self):
        raise NotImplementedError()

    @staticmethod
    def get_name():
        raise NotImplementedError()

    def get_url(self):
        raise NotImplementedError()

    def get_repo_url(self):
        raise NotImplementedError()

    def publish(self):
        logging.info("Publishing '%s' to %s", self.repo, self)
        config = self.repo.get_config()
        if self.identity_file is not None and self.identity_file != '':
            config['identity_file'] = self.identity_file.name

    class Meta:
        abstract = True


class SshStorage(AbstractSshStorage):
    username = models.CharField(max_length=64, validators=[UsernameValidator()])
    edit_url_name = 'storage_ssh_update'
    delete_url_name = 'storage_ssh_delete'

    def get_remote_url(self):
        return '%s@%s:%s' % (self.username, self.host, self.path)

    @staticmethod
    def get_name():
        return _("SSH Storage")

    def get_url(self):
        return self.url

    def get_repo_url(self):
        return self.get_url()  # TODO find out whether to add REPO_DIR or not

    def publish(self):
        super(SshStorage, self).publish()
        local = self.repo.get_repo_path()
        remote = self.get_remote_url()
        server.update_serverwebroot(remote, local)


class GitStorage(AbstractSshStorage):
    edit_url_name = 'storage_git_update'
    delete_url_name = 'storage_git_delete'

    def get_remote_url(self):
        return 'git@%s:%s.git' % (self.host, self.path)

    @staticmethod
    def get_name():
        return _("Git Storage")

    def get_url(self):
        return self.url

    def get_repo_url(self):
        return self.get_url() + '/' + REPO_DIR

    def publish(self):
        super(GitStorage, self).publish()
        remote = [self.get_remote_url()]  # a list is expected
        server.update_servergitmirrors(remote, REPO_DIR)


class StorageManager:
    # register additional storage models here
    storage_models = [S3Storage, SshStorage, GitStorage]

    @staticmethod
    def get_storage(repo):
        """
        Returns all remote storage that belongs to the given repository :param: repo.
        """
        storage = []
        for storage_type in StorageManager.storage_models:
            objects = storage_type.objects.filter(repo=repo).all()
            if objects:
                storage.extend(list(chain(objects)))

        if hasattr(settings, 'DEFAULT_REPO_STORAGE') and settings.DEFAULT_REPO_STORAGE:
            for s in settings.DEFAULT_REPO_STORAGE:
                storage.append(DefaultStorage(repo, s[0], s[1]))

        return storage

    @staticmethod
    def add_to_config(repo, config):
        """
        Adds storage locations to config as mirrors.

        This is done separately, because it requires extra database lookups.
        """
        config['mirrors'] = []
        for storage in StorageManager.get_storage(repo):
            config['mirrors'].append(storage.get_repo_url())


class DefaultStorage:
    is_default = True

    def __init__(self, repo, path, url):
        self.repo = repo
        self.path = path
        self.url = url

    def __str__(self):
        return self.get_name() + " - " + str(self.repo)

    @staticmethod
    def get_name():
        return _('Default Storage')

    def get_url(self):
        return self.url

    def get_repo_url(self):
        return self.get_url() + get_repo_root_path(self.repo) + "/" + REPO_DIR

    def publish(self):
        logging.info("Publishing '%s' to %s", self.repo, self)
        self.repo.get_config()
        local = self.repo.get_repo_path()
        remote = os.path.join(self.path, get_repo_root_path(self.repo))
        if not os.path.exists(remote):
            os.makedirs(remote)
        server.update_serverwebroot(remote, local)
