import logging
from django.test import TestCase
from django.utils import timezone
from django.conf import settings
from .models import WeeblyUser, WeeblySite, WeeblyAuth, WeeblyPaymentNotification
from . import refresh

logger = logging.getLogger(__name__)


class WeeblyTest(TestCase):
    @staticmethod
    def setup_user_site(testcase):
        """
        For testing from another TestCase.
        Objects may already be loaded from a fixture, in that case it will not create new ones.
        """
        user_id = settings.WEEBLY_TEST_USER
        site_id = settings.WEEBLY_TEST_SITE
        site_domain = settings.WEEBLY_TEST_DOMAIN
        auth_token = settings.WEEBLY_TEST_AUTH_TOKEN

        logger.debug(f'weebly test data: user {user_id} - site {site_id} - {site_domain} - auth {auth_token}')

        testcase.weebly_user = WeeblyUser.objects.filter(user_id=user_id).first()
        if not testcase.weebly_user:
            testcase.weebly_user = WeeblyUser.objects.create(user_id=user_id,
                                                             name='Martin M',
                                                             email='martinm@email1234.com')

        testcase.weebly_site = WeeblySite.objects.filter(site_id=site_id).first()
        if not testcase.weebly_site:
            testcase.weebly_site = WeeblySite.objects.create(site_id=site_id,
                                                             user=testcase.weebly_user,
                                                             site_title='Weebly Developer Test Site',
                                                             domain=site_domain,
                                                             is_published=True)
        testcase.weebly_auth =WeeblyAuth.objects.filter(user=testcase.weebly_user, site=testcase.weebly_site).first()
        if not testcase.weebly_auth:
            testcase.weebly_auth = WeeblyAuth.objects.create(user=testcase.weebly_user,
                                                             site=testcase.weebly_site,
                                                             auth_token=auth_token,
                                                             timestamp=timezone.now())

    def setUp(self):
        WeeblyTest.setup_user_site(self)

    def test_refresh(self):
        site = self.weebly_site
        response_json = [
            {'page_id': '1', 'title': 'page 1', 'page_order': 1, 'parent_id': None, 'layout': 'header', 'page_url': 'page-1.html'},
            {'page_id': '2', 'title': 'page 2', 'page_order': 2, 'parent_id': None, 'layout': 'header', 'page_url': 'page-2.html'},
            {'page_id': '3', 'title': 'page 3', 'page_order': 3, 'parent_id': None, 'layout': 'header', 'page_url': 'page-3.html'},
            {'page_id': '4', 'title': 'page 4', 'page_order': 4, 'parent_id': None, 'layout': 'header', 'page_url': 'page-4.html'},
        ]
        refresh.refresh_pages_from_data(site, response_json)
        pages = site.pages.all()
        self.assertEqual(len(pages), 4)
        self.assertEqual(pages[0].page_id, 1)
        self.assertEqual(pages[0].page_order, 1)

        response_json = [
            {'page_id': '1', 'title': 'page 1', 'page_order': 1, 'parent_id': None, 'layout': 'header', 'page_url': 'page-1.html'},
            {'page_id': '2', 'title': 'page 2', 'page_order': 3, 'parent_id': None, 'layout': 'header', 'page_url': 'page-2.html'},
            {'page_id': '3', 'title': 'page 3', 'page_order': 2, 'parent_id': None, 'layout': 'header', 'page_url': 'page-3.html'},
        ]
        refresh.refresh_pages_from_data(site, response_json)
        pages = site.pages.all()
        self.assertEqual(len(pages), 3)
        self.assertEqual(pages[1].page_id, 2)
        self.assertEqual(pages[1].page_order, 3)

        response_json = [
            {'page_id': '4', 'title': 'page 4', 'page_order': 4, 'parent_id': None, 'layout': 'header', 'page_url': 'page-4.html'},
            {'page_id': '5', 'title': 'page 5', 'page_order': 5, 'parent_id': None, 'layout': 'header', 'page_url': 'page-5.html'},
        ]
        refresh.refresh_pages_from_data(site, response_json)
        pages = site.pages.all()
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].page_id, 4)
        self.assertEqual(pages[0].page_order, 4)
        self.assertEqual(pages[1].page_id, 5)
        self.assertEqual(pages[1].page_order, 5)

        site.refresh_pages()

    def create_payment_notification(self, gross_amount):
        notification = WeeblyPaymentNotification.objects.create(
            site=self.weebly_site,
            name='test payment',
            detail='this is a test payment',
            purchase_not_refund=True,
            kind=WeeblyPaymentNotification.PaymentKind.SETUP,
            term=WeeblyPaymentNotification.PaymentTerm.FOREVER,
            gross_amount=gross_amount
        )
        self.assertEqual(notification.payable_amount, gross_amount * 0.3, 'payable should be 30%')
        self.assertFalse(notification.notified_to_weebly)
        return notification

    def test_payment_notification(self):
        self.assertFalse(settings.PRODUCTION, 'this test can not be run in production')
        logger.info('testing notifying a single payment')
        notification = self.create_payment_notification(10)
        rv = notification.notify()
        self.assertFalse('error' in rv)
        self.assertTrue(notification.notified_to_weebly)

    def test_notify_non_notified(self):
        self.assertFalse(settings.PRODUCTION, 'this test can not be run in production')
        logger.info('testing notify non-notified')
        self.create_payment_notification(10)
        self.create_payment_notification(100)
        self.assertEqual(WeeblyPaymentNotification.objects.filter(notified_to_weebly=False).count(), 2,
                         'There should be non-notified payments')
        WeeblyPaymentNotification.notify_unnotified()
        self.assertEqual(WeeblyPaymentNotification.objects.filter(notified_to_weebly=False).count(), 0,
                         'There should not be any non-notified payments')