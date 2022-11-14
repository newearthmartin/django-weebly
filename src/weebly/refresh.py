import logging
import re
import json
from django.db import models
from marto_python.util import as_datetime
from marto_python.strings import as_int
from marto_python.email.email import send_email_to_admins
from .util import update_list_of, update_object_from_data, \
    unescape_func_not_null, url_func, unescape_func, unescape_dict_val_func,  valid_email_func
from . import models


logger = logging.getLogger(__name__)


def update_from_data(obj, data, data_obj_mapping):
    """
    update an object from weebly data, returns true if changed
    """
    changed = False
    for mapping in data_obj_mapping:
        data_attr = mapping[0]
        obj_attr = mapping[1]
        mapping_fn = mapping[2] if len(mapping) >= 3 else None

        json_val = data[data_attr]
        old_val = getattr(obj, obj_attr)
        new_val = mapping_fn(json_val) if mapping_fn else json_val

        if old_val != new_val:
            changed = True
            setattr(obj, obj_attr, new_val)
    return changed


def refresh_user(user, weebly_auth):
    if not weebly_auth:
        weebly_auth = user.get_default_weebly_auth()
    rv, resp_json = weebly_auth.weebly_request('/v1/user',
                                               params={'user_id': user.user_id},
                                               action_name='getting user details')
    if 'error' not in rv:
        changed = update_from_data(user, resp_json, [
            ('user_id', 'user_id', as_int),
            ('name', 'name', unescape_func),
            ('email', 'email', valid_email_func),
        ])
        if changed: user.save()
    return rv


def refresh_site(site, weebly_auth=None):
    if not weebly_auth:
        weebly_auth = site.get_default_weebly_auth()
    rv, resp_json = weebly_auth.weebly_request(f'/v1/user/sites/{site.site_id}',
                                               action_name='getting site details',
                                               expected_errors=['Site not found'])
    changed = False
    if 'error' not in rv:
        def lang_func(l):
            if not isinstance(l, str):
                logger.error(f'weebly is returning {l} as language - json: {resp_json}')
                return site.language
            return l

        changed = update_from_data(site, resp_json, [
            ('site_id', 'site_id', as_int),
            ('site_title', 'site_title', unescape_func),
            ('domain', 'domain'),
            ('is_published', 'is_published'),
            ('language', 'language', lang_func),
            ('user_id', 'user', lambda val: models.WeeblyUser.get_or_create(val))
        ])
        if not site.is_found:
            site.is_found = True
            changed = True
    elif 'Site not found' in rv['error']:
        if site.is_found:
            site.is_found = False
            changed = True
    if changed:
        logger.info(f'{site} - changed, saving and sending signal')
        site.save()
        site.signal_change()
    return rv


def refresh_pages(site, weebly_auth=None, signal_change=False):
    if not weebly_auth:
        weebly_auth = site.get_default_weebly_auth()
    logger.info(f'{site} - refreshing pages')
    path = f'/v1/user/sites/{site.site_id}/pages'
    rv, response_json = weebly_auth.weebly_request_paginated(path, action_name='requesting pages')
    if 'error' in rv:
        return rv
    return refresh_pages_from_data(site, response_json, signal_change=signal_change)


def refresh_pages_from_data(site, pages_data, signal_change=False):
    rv = update_list_of(
        site,
        pages_data,
        'page', 'page_id', 'page_id',
        lambda: models.WeeblyPage(site=site),
        site.pages,
        [
            ('title', 'title', unescape_func_not_null),
            ('page_url', 'page_url', url_func),
            ('page_order', 'page_order', None),
            ('parent_id', 'parent_id', as_int),
        ]
    )
    changes = 'changes' in rv and rv['changes']
    if signal_change and changes: site.signal_change()
    return rv


def refresh_blogs(site, refresh_posts=False):
    weebly_auth = site.get_default_weebly_auth()
    path = f'/v1/user/sites/{site.site_id}/blogs'
    expected_errors = ['access to the requested user information']  # TODO: remove expected_errors when permissions are ok
    rv, resp_json = weebly_auth.weebly_request(path, action_name='getting blogs', expected_errors=expected_errors)
    if 'error' in rv:
        return rv
    rv = update_list_of(
        site,
        resp_json,
        'blog', 'blog_id', 'blog_id',
        lambda: models.WeeblyBlog(site=site),
        site.blogs,
        [
            ('blog_id', 'blog_id', as_int),
            ('page_id', 'page_id', as_int),
            ('title', 'title', unescape_func),
        ]
    )
    if 'error' in rv:
        return rv
    changes = rv.get('changes', False)
    if refresh_posts:
        for blog in site.blogs.all():
            rv = blog.refresh_posts()
            changes = changes or rv.get('changes', False)
            if 'error' in rv:
                return rv
        for blog in site.blogs.all():
            for post in blog.posts.all():
                rv = post.refresh_from_weebly()
                if 'error' in rv:
                    return rv
                changes = changes or rv.get('changes', False)
    return {'changes': changes}


def refresh_posts(blog):
    site = blog.site
    weebly_auth = site.get_default_weebly_auth()
    path = f'/v1/user/sites/{site.site_id}/blogs/{blog.blog_id}/posts'
    rv, resp_json = weebly_auth.weebly_request(path, action_name='getting blog posts')
    if 'error' in rv:
        return rv
    rv = update_list_of(site, resp_json, 'post', 'post_id', 'post_id', lambda: models.WeeblyBlogPost(blog=blog),
                        blog.posts,
                        [
                            ('post_id', 'post_id', as_int),
                            ('post_title', 'title', unescape_func),
                            ('created_date', 'created_date', as_datetime)
                        ])
    return rv


def refresh_post(post):
    site = post.blog.site
    weebly_auth = site.get_default_weebly_auth()
    path = f'/v1/user/sites/{site.site_id}/blogs/{post.blog.blog_id}/posts/{post.post_id}'
    rv, resp_json = weebly_auth.weebly_request(path, action_name='getting blog post details')

    def tags_fn(tags):
        if not tags: tags = {}
        tags = unescape_dict_val_func(tags)
        return json.dumps(tags)

    if 'error' not in rv:
        changes = update_from_data(post, resp_json, [
            ('post_id', 'post_id', as_int),
            ('post_title', 'title', unescape_func),
            ('created_date', 'created_date', as_datetime),
            ('updated_date', 'updated_date', as_datetime),
            ('post_body', 'body', unescape_func),
            ('post_link', 'link', unescape_func),
            ('post_url', 'url', unescape_func),
            ('seo_title', 'seo_title', unescape_func),
            ('seo_description', 'seo_description', unescape_func),
            ('tags', 'tags', tags_fn),
        ])
        if changes:
            post.save()
        rv = {'changes': changes}
    return rv


def refresh_store(site, weebly_auth=None, signal_change=False):
    if not weebly_auth:
        weebly_auth = site.get_default_weebly_auth()
    rv = site.refresh_store_products(weebly_auth)
    if 'error' in rv: return rv
    changes = 'changes' in rv and rv['changes']
    for product in site.store_products.all():
        rv = product.refresh_from_weebly(weebly_auth)
        if 'error' in rv: return rv
        changes = changes or ('changes' in rv and rv['changes'])
    rv = site.refresh_store_categories(weebly_auth=weebly_auth)
    if 'error' in rv: return rv
    changes = changes or ('changes' in rv and rv['changes'])
    if signal_change and changes: site.signal_change()
    return {'changes': changes}


def refresh_store_products(site, weebly_auth=None, signal_change=False):
    if not weebly_auth:
        weebly_auth = site.get_default_weebly_auth()
    logger.info(f'{site} - refreshing store products')
    path = f'/v1/user/sites/{site.site_id}/store/products'
    rv, response_json = weebly_auth.weebly_request_paginated(path, action_name='requesting store products')
    if 'error' in rv: return rv
    rv = update_list_of(
        site,
        response_json,
        'store product',
        'product_id', 'product_id',
        lambda: models.WeeblyStoreProduct(site=site),
        site.store_products,
        [
            ('name', 'name', unescape_func_not_null),
            ('url', 'url', None)
        ]
    )
    changes = 'changes' in rv and rv['changes']
    if signal_change and changes: site.signal_change()
    return rv


def refresh_store_product(product, weebly_auth):
    site = product.site
    logger.info(f'{site} - refreshing product {product.product_id}')
    path = f'/v1/user/sites/{site.site_id}/store/products/{product.product_id}'
    rv, resp_json = weebly_auth.weebly_request(path, action_name=f'requesting product info - {product.product_id}')
    if 'error' in rv: return rv
    properties_mapping = [
        ('name', 'name', unescape_func_not_null),
        ('url', 'url', None),
        ('short_description', 'description', None),
    ]
    save = update_object_from_data(product, properties_mapping, resp_json)
    if save:
        logger.info(f'{site} - saving product {product.product_id}')
        product.save()
    rv2 = refresh_product_options_from_data(product, resp_json['options'])
    if 'error' in rv2: return rv2
    rv['changes'] = save or ('changes' in rv2 and rv2['changes'])
    return rv


def refresh_product_options(product, weebly_auth):
    """
    This function is not being used. Refreshing a product refreshes the options.
    Leaving it for now.
    """
    site = product.site
    logger.info(f'{site} - refreshing product {product.product_id} - options')
    path = f'/v1/user/sites/{site.site_id}/store/products/{product.product_id}/options'
    rv, resp_json = weebly_auth.weebly_request(path, action_name='requesting product options')
    if 'error' in rv: return rv
    return refresh_product_options_from_data(product, resp_json)


def refresh_product_options_from_data(product, data):
    color_option_regex = re.compile('(.+)(<#.+>)')

    def choices_func(choices):
        new_choices = []
        for choice in choices:
            if choice.startswith('Text:'): continue
            match_color = re.search(color_option_regex, choice)
            if match_color:
                choice = match_color.group(1)
            new_choices.append(choice)
        return json.dumps(new_choices)

    return update_list_of(
        product.site, data,
        'store product option',
        'option_id', 'product_option_id',
        lambda: models.WeeblyStoreProductOption(product=product),
        product.options,
        [
            ('name', 'name', unescape_func_not_null),
            ('choice_order', 'choices', choices_func)
        ])


def refresh_store_categories(site, weebly_auth=None, signal_change=False):
    if not weebly_auth:
        weebly_auth = site.get_default_weebly_auth()
    logger.info(f'{site} - refreshing store categories')
    path = f'/v1/user/sites/{site.site_id}/store/categories'
    rv, response_json = weebly_auth.weebly_request_paginated(path, action_name='requesting store categories')
    if 'error' in rv: return rv

    loops = 0
    changes = False
    while True:
        missing_cats = []

        def parent_category_func(parent_category_id):
            if not parent_category_id: return None
            parent_category_id = int(parent_category_id)
            parent_cat = models.WeeblyStoreCategory.objects.filter(site=site, category_id=parent_category_id)
            if parent_cat.exists():
                return parent_cat.first()
            else:
                msg = f'{site} - reference to a category that doesn\'t exist, probably defined later'
                logger.warning(msg)
                missing_cats.append(parent_category_id)
                return None

        site.refresh_from_db()  # FIXME: lets see if this fixes everything :P
        rv = update_list_of(
            site,
            response_json,
            'store category',
            'category_id',
            'category_id',
            lambda: models.WeeblyStoreCategory(site=site),
            site.store_categories,
            [
                ('name', 'name', unescape_func_not_null),
                ('parent_category_id', 'parent_category', parent_category_func)
            ]
        )
        if 'error' in rv: return rv
        changes = changes or ('changes' in rv and rv['changes'])
        loops += 1
        if not missing_cats: break
        if loops == 3:
            # FIXME: this really shouldnt happen, but it does, so need to understand what's going on
            logger.warning('already 3 times looping, breaking')
            send_email_to_admins('already 3 times looping', json.dumps(response_json))
            break
    if signal_change and changes: site.signal_change()
    return {'changes': changes}
