import logging
import json
import jwt
import requests
from datetime import timedelta
from collections.abc import Iterable
from django.conf import settings
from django.contrib.admin import ModelAdmin
from django.urls import reverse
from django.db import models
from django.db.models import Model
from django.db.models import CASCADE
from django.utils.safestring import mark_safe
from django.utils import timezone
from django.utils.html import format_html
from tinymce import models as tinymce_models

from marto_python.strings import to_decimal
from marto_python.threads import RunInThread

from .signals import site_refreshed_signal

from . import refresh

logger = logging.getLogger(__name__)


def domain_decorator(title='domain', admin_order_field=None):
    """
    makes a domain clickable in admin
    """
    def real_decorator(func):
        @mark_safe
        def decorated(*args, **kwargs):
            val = func(*args, **kwargs)
            if not val:
                return None
            elif isinstance(val, str):
                domains = [val]
            elif isinstance(val, Iterable):
                domains = val
            else:
                return None

            domains = [d if d.startswith('http') else f'http://{d}' for d in domains]
            domains = [f'<a href="{d}" target="_blank">{d}</a>' for d in domains]
            return format_html(' '.join(domains))

        decorated.short_description = title
        if admin_order_field:
            decorated.admin_order_field = admin_order_field
        return decorated

    return real_decorator


# noinspection PyClassHasNoInit
class SiteDomainMixin:
    """
    Mixin for admin classes that adds the site_domain property
    """
    @mark_safe
    @domain_decorator(title='site domain', admin_order_field='site__domain')
    def site_domain(self, obj):
        return obj.site.domain if obj.site else None


class AccountSiteDomainMixin:
    """
    Mixin for admin classes that adds the site_domain property, through the account property
    """
    @mark_safe
    @domain_decorator(title='site domain', admin_order_field='account__site__domain')
    def site_domain(self, obj):
        return obj.account.site.domain if obj.account else None


class PageSiteDomainMixin:
    """
    Mixin for admin classes that adds the site_domain property, through the page property
    """
    @mark_safe
    @domain_decorator(title='site domain', admin_order_field='page__site__domain')
    def site_domain(self, obj):
        return obj.page.weebly_page.site.domain


class WeeblyUser(Model):
    user_id = models.BigIntegerField()
    name = models.CharField(max_length=256, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)

    def __str__(self):
        return str(self.user_id)

    def refresh_from_weebly(self, weebly_auth=None):
        return refresh.refresh_user(self, weebly_auth)

    def get_default_weebly_auth(self):
        return self.weeblyauth_set.filter(is_valid=True).last()

    @staticmethod
    def get_or_create(user_id):
        user = WeeblyUser.objects.filter(user_id=user_id)
        if user.exists():
            return user.get()
        else:
            user = WeeblyUser(user_id=user_id)
            user.save()
            return user

    class Admin(ModelAdmin):
        list_display = ['user_id', 'name', 'email']
        search_fields = ['user_id', 'name', 'email']
        actions = ['refresh_from_weebly']

        @RunInThread
        def refresh_from_weebly(self, _, queryset):
            for user in queryset:
                weebly_auth = user.get_default_weebly_auth()
                if not weebly_auth:
                    logger.info(f'{user} - no valid weebly auth found for user')
                    continue
                user.refresh_from_weebly(weebly_auth=weebly_auth)


class WeeblySite(Model):
    site_id = models.BigIntegerField()
    user = models.ForeignKey(WeeblyUser, null=True, blank=True, on_delete=CASCADE)
    site_title = models.CharField(max_length=1024, null=True, blank=True)
    domain = models.CharField(max_length=512, null=True, blank=True)
    is_published = models.BooleanField(default=False)
    language = models.CharField(max_length=8, null=True, blank=True)
    is_found = models.BooleanField(default=True)

    def refresh_from_weebly(self, weebly_auth=None): return refresh.refresh_site(self, weebly_auth=weebly_auth)
    def refresh_pages(self, weebly_auth=None, signal_change=False): return refresh.refresh_pages(self, weebly_auth=weebly_auth, signal_change=signal_change)
    def refresh_blogs(self, refresh_posts=False): return refresh.refresh_blogs(self, refresh_posts=refresh_posts)
    def refresh_store_products(self, weebly_auth=None, signal_change=False): return refresh.refresh_store_products(self, weebly_auth=weebly_auth, signal_change=signal_change)
    def refresh_store_categories(self, weebly_auth=None, signal_change=False): return refresh.refresh_store_categories(self, weebly_auth=weebly_auth, signal_change=signal_change)
    def refresh_store(self, weebly_auth=None, signal_change=False): return refresh.refresh_store(self, weebly_auth=weebly_auth, signal_change=signal_change)

    def signal_change(self):
        logger.info(f'{self} - sending change signal')
        site_refreshed_signal.send(sender=self)

    def __str__(self):
        return f'site_{self.site_id}'

    def get_default_weebly_auth(self):
        qs = self.weeblyauth_set
        auth_for_user = qs.filter(user=self.user).last()
        return auth_for_user if auth_for_user else qs.last()

    @staticmethod
    def get_or_create(site_id):
        site = WeeblySite.objects.filter(site_id=site_id)
        if site.exists():
            return site.get()
        else:
            site = WeeblySite(site_id=site_id)
            site.save()
            return site

    class Admin(ModelAdmin):
        list_display = ['site_id', 'site_title', 'domain', 'user', 'is_published', 'is_found', 'language']
        list_filter = ['is_published', 'language']
        search_fields = ['site_id', 'domain', 'user__user_id', 'user__email']
        actions = ['refresh_from_weebly']

        @RunInThread
        def refresh_from_weebly(self, _, queryset):
            for site in queryset:
                weebly_auth = site.get_default_weebly_auth()
                site.refresh_from_weebly(weebly_auth)


class WeeblyAuth(Model):
    user = models.ForeignKey(WeeblyUser, on_delete=CASCADE)
    site = models.ForeignKey(WeeblySite, on_delete=CASCADE)
    auth_token = models.CharField(max_length=100, null=True, blank=True)
    timestamp = models.DateTimeField(null=True, blank=True)
    is_valid = models.BooleanField(default=True)
    version = models.CharField(max_length=20)

    def user_email(self): return self.user.email

    def user_name(self): return self.user.name

    def refresh_from_weebly(self):
        self.user.refresh_from_weebly(weebly_auth=self)
        self.site.refresh_from_weebly(weebly_auth=self)

    @staticmethod
    def get_for_site(site):
        return WeeblyAuth.objects.filter(is_valid=True, site=site).last()

    @mark_safe
    def get_user_id(self):
        change_user_url = reverse('admin:weebly_weeblyuser_change', args=[self.user.pk])
        return f'<a href="{change_user_url}">{self.user.pk}</a>'
    get_user_id.short_description = 'user'
    get_user_id.admin_order_field = 'user'

    @mark_safe
    def site_domain(self):
        return f'<a href="http://{self.site.domain}" target="_blank">{self.site.domain}</a>'

    @mark_safe
    def get_site_id(self):
        change_site_url = reverse('admin:weebly_weeblysite_change', args=[self.site.pk])
        return f'<a href="{change_site_url}">{self.site.site_id}</a>'
    get_site_id.short_description = 'site'
    get_site_id.admin_order_field = 'site'

    def get_jwt_token(self, exp_minutes=None):
        payload = {'user_id': self.user.user_id, 'site_id': self.site.site_id}
        if exp_minutes is not None:
            exp = timezone.now() + timedelta(minutes=exp_minutes)
            payload['exp'] = int(exp.timestamp())
        return jwt.encode(payload, settings.WEEBLY_SECRET, algorithm='HS256')

    class Meta:
        unique_together = [['user', 'site']]

    class Admin(ModelAdmin):
        list_display = ['pk', 'get_user_id', 'get_site_id', 'user_name', 'user_email', 'site_domain', 'is_valid',
                        'version', 'auth_token', 'timestamp']
        list_filter = ['is_valid', 'timestamp', 'version']
        search_fields = ['id', 'user__user_id', 'user__name', 'user__email', 'site__site_id', 'site__domain']
        actions = ['refresh_from_weebly', 'deauthorize']

        @RunInThread
        def refresh_from_weebly(self, _, queryset):
            for weebly_auth in queryset:
                weebly_auth.refresh_from_weebly()

        @RunInThread
        def deauthorize(self, _, queryset):
            for weebly_auth in queryset:
                weebly_auth.deauthorize()

    def __str__(self):
        return f'user_{self.user.user_id}-site_{self.site.site_id}'

    @staticmethod
    def get_or_create(user_id, site_id, version):
        weebly_auth = WeeblyAuth.objects.filter(user__user_id=user_id, site__site_id=site_id).first()
        if not weebly_auth:
            user = WeeblyUser.get_or_create(user_id)
            site = WeeblySite.get_or_create(site_id)
            weebly_auth = WeeblyAuth(user=user, site=site, version=version)
            weebly_auth.save()
        else:
            if version and version != weebly_auth.version:
                weebly_auth.version = version
                weebly_auth.save()
        return weebly_auth

    def __make_weebly_request(self, path, params=None, method='get', data=None, action_name='doing weebly request'):
        if not params: params = {}
        if not data: data = {}
        if not self.is_valid: logger.warning(f'{self} - making request with invalid weebly auth')
        headers = {
            'Accept': 'application/vnd.weebly.v1+json',
            'User-Agent': settings.WEEBLY_APP_NAME,
            'X-Weebly-Access-Token': self.auth_token,
        }
        url = 'https://api.weebly.com' + path
        logger.info(f'{action_name} - {method.upper()} {url}')
        return requests.request(method, url, headers=headers, params=params, json=data, timeout=60)

    def __handle_response(self,
                          resp,
                          action_name='doing weebly request',
                          expected_errors=None):
        if not expected_errors: expected_errors = []
        rv = {}
        try:
            resp_json = resp.json()
        except ValueError:
            logger.warning(f'no JSON response - {action_name} - {resp.text}')
            return {'error': 'invalid JSON response'}, None

        if resp.status_code == 200:
            logger.debug(f'{action_name} - OK')
        else:
            rv_error = action_name
            if 'error' in resp_json:
                resp_error = resp_json['error']['message']
                rv_error += f' - {resp_error}'
                msg = f'{rv_error} - {self}'
                error_is_expected = False
                for error in expected_errors + ['Unknown api key']:
                    if error in msg:
                        error_is_expected = True
                        break
                logger.log(logging.WARN if error_is_expected else logging.ERROR, msg)
            rv['error'] = 'Error ' + rv_error
        self.check_still_valid(rv)
        return rv, resp_json

    def weebly_request(self,
                       path, params=None, method='get', data=None,
                       action_name='doing weebly request',
                       expected_errors=None):
        """
        helper method combination of the previous two
        """
        if not params: params = {}
        if not data: data = {}
        try:
            resp = self.__make_weebly_request(path, params, method, data, action_name)
            return self.__handle_response(resp, action_name, expected_errors=expected_errors)
        except requests.RequestException as e:
            message = f'{type(e)} - {e}'
            logger.error(f'request error while {action_name}: {message}', exc_info=True)
            return {'error': message}, None

    def weebly_request_paginated(self,
                                 path, params=None, method='get', data=None,
                                 action_name='doing weebly request',
                                 limit_count=200, expected_errors=None):
        """
        helper method for doing paginated requests
        """
        if not params: params = {}
        if not data: data = {}
        page = 1
        response_json = []
        new_params = params.copy()
        new_params['limit'] = limit_count
        while True:
            new_params['page'] = page
            rv, resp_json = self.weebly_request(path,
                                                params=new_params,
                                                method=method,
                                                data=data,
                                                action_name=action_name,
                                                expected_errors=expected_errors)
            if 'error' in rv:
                return rv, response_json
            response_json += resp_json
            page += 1
            if len(resp_json) < limit_count:
                return {}, response_json
            logger.info(f'paginated request to weebly, page {page}')

    def publish_site(self):
        path = f'/v1/user/sites/{self.site.site_id}/publish'
        rv, _ = self.weebly_request(path, method='post', action_name='publishing site', expected_errors=[
            'This site cannot be published',
            'Questo sito non pu?? essere pubblicato',
            'CAPTCHA',
            'Product count is too high',
            'Produktanzahl ist zu hoch',
            '??????????????????',
            'Unable to build new Snapshot',
            'Member count is too high',
            'findShard failed',
            'Account Verification',
            'Accountverificatie',
            'Este site n??o pode ser publicado',
            'Es necesario verificar la cuenta antes de publicar',
            'El n??mero de suscripciones es demasiado alto',
            'Le nombre de membre est trop ??lev??',
        ])
        return rv

    def publish_snippet(self, snippet):
        path = f'/v1/user/sites/{self.site.site_id}/snippet'
        rv, _ = self.weebly_request(path, method='post', data={'snippet': snippet}, action_name='publishing snippet')
        return rv

    def update_card(self, card_name, card_data, hidden=False):
        url = f'/v1/user/sites/{self.site.site_id}/cards/{card_name}'
        data = {
            'hidden': hidden,
            'card_data': card_data
        }
        rv, _ = self.weebly_request(url, method='patch', data=data, action_name='updating card')
        return rv

    def deauthorize(self):
        path = f'/v1/user/sites/{self.site.site_id}/apps/{settings.WEEBLY_CLIENT_ID}/deauthorize'
        rv, resp_json = self.weebly_request(path, method='post', action_name='deauthorizing')
        if 'error' not in rv:
            status = resp_json.get('status', None)
            if status != 'disconnected':
                logger.error(f'{self} - Attempted to disconnect but status is {status}')
        return rv

    def check_still_valid(self, rv):
        """
        Sets the is_valid flag according to if we get 'Unknown api key' in the response
        """
        if 'error' not in rv:
            if not self.is_valid:
                self.is_valid = True
                self.save()
        elif 'Unknown api key' in rv['error']:
            if self.is_valid:
                logger.warning(f'{self} - weebly auth is no longer valid')
                self.is_valid = False
                self.save()


class WeeblyPage(Model):
    site = models.ForeignKey(WeeblySite, on_delete=CASCADE, related_name='pages')
    page_id = models.BigIntegerField(unique=True)
    title = models.CharField(max_length=512)
    page_url = models.CharField(max_length=1024, blank=True, null=True)
    hidden = models.BooleanField(default=False)
    page_order = models.IntegerField(default=0)
    parent_id = models.BigIntegerField(blank=True, null=True)
    
    def site_domain(self):
        return self.site.domain
    
    def __str__(self):
        return self.title
    
    def is_link(self):
        if not self.page_url: return False
        url_lower = self.page_url.lower()
        return url_lower.startswith('http://') or url_lower.startswith('https://')

    def get_parent(self):
        if not self.parent_id: return None
        return WeeblyPage.objects.filter(page_id=self.parent_id).first()

    def children_qs(self):
        return WeeblyPage.objects.filter(parent_id=self.page_id)

    def all_descendents(self):
        current = self.children_qs().all()
        while current:
            new_current = []
            for page in current:
                yield page
                new_current += page.children_qs().all()
            current = new_current

    def all_ancestors(self):
        current = self
        while current:
            parent = current.get_parent()
            if parent:
                yield parent
            current = parent

    def total_order(self):
        order = [self.page_order]
        parent = self
        while True:
            parent = parent.get_parent()
            if not parent: break
            order = [parent.page_order] + order
        return order

    def total_url(self):
        if self.is_link():
            return self.page_url
        elif self.page_url:
            return f'http://{self.site.domain}{self.page_url}'
        else:
            return None

    class Admin(ModelAdmin, SiteDomainMixin):
        list_display = ['pk', 'page_id', 'title', 'site_domain', 'total_order', 'path']
        list_filter = []
        search_fields = ['page_id', 'title', 'site__site_id', 'site__domain']
        readonly_fields = ['site', 'site_domain']

        @mark_safe
        def path(self, obj):
            return f'<a href="http://{obj.site.domain}{obj.page_url}" target="_blank">{obj.page_url}</a>'
        path.admin_order_field = 'page_url'


class WeeblyPaymentNotification(Model):
    class PaymentTerm(models.TextChoices):
        MONTH = 'month', 'Month'
        YEAR = 'year', 'Year'
        FOREVER = 'forever', 'Forever'
        REFUND = 'refund', 'Refund'

    class PaymentKind(models.TextChoices):
        SINGLE = 'single', 'Single'
        SETUP = 'setup', 'Setup'

    site = models.ForeignKey(WeeblySite, on_delete=CASCADE)
    name = models.CharField(max_length=256)
    detail = models.CharField(max_length=256, blank=True, null=True)
    purchase_not_refund = models.BooleanField(default=True)
    kind = models.CharField(max_length=10, choices=PaymentKind.choices, blank=True, null=True)
    term = models.CharField(max_length=10, choices=PaymentTerm.choices, blank=True, null=True)
    gross_amount = models.DecimalField(max_digits=10, decimal_places=2)
    payable_amount = models.DecimalField(max_digits=10, decimal_places=2, blank=True)
    currency = models.CharField(max_length=10, default='USD', blank=True, null=True)
    notified_to_weebly = models.BooleanField(default=False)
    notified_to_weebly_on = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'payment_for_site_{self.site.site_id}_{self.payable_amount}_{self.currency}'

    # noinspection PyUnusedLocal
    @staticmethod
    def pre_save(sender, instance, **kwargs):
        instance.payable_amount = to_decimal(instance.gross_amount * to_decimal(0.3, 2), 2)

    @staticmethod
    def notify_unnotified():
        unnotified_count = WeeblyPaymentNotification.objects.filter(notified_to_weebly=False).count()
        if unnotified_count == 0:
            logger.info('There are no unnotified payments')
        else:
            logger.info(f'Notifying {unnotified_count} unnotified payments')
        for notif in WeeblyPaymentNotification.objects.filter(notified_to_weebly=False).all():
            logger.info(f'notifying {notif}')
            notif.notify()

    def notify(self):
        if self.notified_to_weebly:
            error = 'trying to notify an already notified payment'
            logger.error(error)
            return {'error': error}
        if self.gross_amount == 0:
            logger.warning('trying to notify a 0 payment, marking as notified')
            self.notified_to_weebly = True
            self.save()
            return {}
        weebly_auth = self.site.get_default_weebly_auth()
        if not weebly_auth.is_valid:
            logger.warning('Invalid weebly auth for notifying payment, using default weebly auth')
            weebly_auth_pk = getattr(settings, 'DEFAULT_WEEBLY_AUTH', None)
            if weebly_auth_pk:
                weebly_auth = WeeblyAuth.objects.get(pk=weebly_auth_pk)
            else:
                logger.error(f'Unable to notify payment {self} - Invalid weebly auth - Missing default weebly auth')
                return {'error': 'Invalid weebly auth'}

        method = 'purchase' if self.purchase_not_refund else 'refund'
        if not settings.PRODUCTION: method = 'test' + method
        path = '/v1/admin/app/payment_notifications'
        data = {
            'name': self.name,
            'method': method,
            'gross_amount': self.gross_amount,
            'payable_amount': self.payable_amount,
        }
        if self.detail: data['detail'] = self.detail
        if self.kind: data['kind'] = self.kind
        if self.term: data['term'] = self.term
        if self.currency: data['currency'] = self.currency
        rv, _ = weebly_auth.weebly_request(path, method='post', data=data, action_name='reporting payment')
        if 'error' not in rv:
            self.notified_to_weebly = True
            self.notified_to_weebly_on = timezone.now()
            self.save()
        else:
            logger.error('error reporting payment: ' + rv['error'])
        return rv

    class Admin(SiteDomainMixin, ModelAdmin):
        list_display = ['pk', 'name', 'term', 'site_domain', 'site', 'gross_amount', 'payable_amount',
                        'notified_to_weebly', 'notified_to_weebly_on']
        list_filter = ['notified_to_weebly', 'notified_to_weebly_on', 'gross_amount', 'payable_amount']
        readonly_fields = ['site', 'site_domain', 'payable_amount']
        search_fields = ['site__site_id', 'site__domain', 'name', 'detail']


class WeeblyBlog(Model):
    site = models.ForeignKey(WeeblySite, related_name='blogs', on_delete=CASCADE)
    blog_id = models.BigIntegerField()
    page_id = models.BigIntegerField()
    title = models.CharField(max_length=256)

    def __str__(self):
        return f'weebly_blog_{self.pk}'

    def refresh_posts(self):
        return refresh.refresh_posts(self)

    def get_post_tags(self):
        rv = {}
        for post in self.posts.all():
            tags = json.loads(post.tags) if post.tags else {}
            rv.update(tags)
        return rv

    class Admin(SiteDomainMixin, ModelAdmin):
        list_display = ['blog_id', 'site_domain', 'title', 'page_id']
        search_fields = ['blog_id', 'page_id', 'site__domain', 'title']
        readonly_fields = ['site', 'site_domain']


class WeeblyBlogPost(Model):
    blog = models.ForeignKey(WeeblyBlog, related_name='posts', on_delete=CASCADE)
    post_id = models.BigIntegerField()
    title = models.CharField(max_length=256)
    created_date = models.DateTimeField(null=True, blank=True)

    updated_date = models.DateTimeField(null=True, blank=True)
    body = models.TextField(null=True, blank=True)
    link = models.CharField(max_length=1024, null=True, blank=True)
    url = models.CharField(max_length=1024, null=True, blank=True)
    share_message = models.CharField(max_length=256, null=True, blank=True)
    seo_title = models.CharField(max_length=256, null=True, blank=True)
    seo_description = models.TextField(null=True, blank=True)
    tags = models.TextField(null=True, blank=True)

    def __str__(self):
        return f'weebly_blog_post_{self.pk}'

    def refresh_from_weebly(self):
        return refresh.refresh_post(self)

    class Admin(ModelAdmin):
        list_display = ['post_id', 'title', 'created_date', 'tags']
        readonly_fields = ['blog']


class WeeblyStoreCategory(Model):
    site = models.ForeignKey(WeeblySite, related_name='store_categories', on_delete=CASCADE)
    category_id = models.BigIntegerField()
    name = models.CharField(max_length=256)
    parent_category = models.ForeignKey('WeeblyStoreCategory', blank=True, null=True, on_delete=CASCADE)

    def __str__(self):
        return self.name

    class Meta:
        unique_together = [['site', 'category_id']]
        verbose_name_plural = 'weebly store categories'

    class Admin(ModelAdmin, SiteDomainMixin):
        list_display = ['pk', 'category_id', 'name', 'parent_category', 'site_domain']
        search_fields = ['category_id', 'name', 'site__site_id', 'site__domain']
        readonly_fields = ['site', 'site_domain']
        # inlines = [StoreCategoryTranslationInline]


class WeeblyStoreProduct(Model):
    site = models.ForeignKey(WeeblySite, related_name='store_products', on_delete=CASCADE)
    product_id = models.BigIntegerField()
    name = models.CharField(max_length=256)
    description = tinymce_models.HTMLField(blank=True, null=True)
    url = models.CharField(max_length=1024, blank=True, null=True)

    def __str__(self):
        return f'product_{self.pk}:{self.name}'

    def refresh_from_weebly(self, weebly_auth=None):
        if not weebly_auth: weebly_auth = self.site.get_default_weebly_auth()
        return refresh.refresh_store_product(self, weebly_auth)

    def refresh_options(self, weebly_auth=None):
        if not weebly_auth: weebly_auth = self.site.get_default_weebly_auth()
        return refresh.refresh_product_options(self, weebly_auth)

    class Meta:
        unique_together = [['site', 'product_id']]


class WeeblyStoreProductOption(Model):
    product = models.ForeignKey(WeeblyStoreProduct, related_name='options', on_delete=CASCADE)
    option_id = models.BigIntegerField()
    name = models.CharField(max_length=256)
    choices = models.TextField()

    def get_choices_array(self):
        return json.dumps(self.choices)

    def set_choices_array(self, array):
        self.choices = json.loads(array)

    def __str__(self): return self.name

    class Meta:
        unique_together = [['product', 'option_id']]
