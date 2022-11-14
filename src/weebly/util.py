import logging
import html
import json
from collections import OrderedDict
from django.db import IntegrityError
from marto_python.email.email import send_email_to_admins
from marto_python.url import is_absolute
from marto_python.util import is_valid_email
from marto_python.collection_utils import map_dict

logger = logging.getLogger(__name__)


def update_list_of(site,
                   json_data,
                   object_name,
                   id_property_elem,
                   id_property_data,
                   new_elem_func,
                   related_manager,
                   properties_mapping):
    """
    updates the list of things in the database according to a response from weebly api

    params:

    site:               the weebly site object
    json_data:          the json data from the weebly api response
    object_name:        the name of the object, for logging
    id_property_elem:   the name of the id property in the model
    id_property_data:   the name of the id property in the json data
    new_elem_func:      function that creates a new object
    related_manager:    the related manager for the list of objects to be updated
    properties_mapping:
        a list of 3-uples for mapping properties in the response to the model
        (response property name, model property name, function that translates from response to model value)
    """

    log_strs = []

    def log(log_msg, log=False):
        if log: logger.info(f'{site} - {log_msg}')
        log_strs.append(log_msg)

    changes = False

    original_elems = OrderedDict()
    data_elems = OrderedDict()
    for original_elem in related_manager.all():
        elem_id = getattr(original_elem, id_property_elem)
        original_elems[elem_id] = original_elem
    for data_elem in json_data:
        data_id = int(data_elem[id_property_data])
        data_elems[data_id] = data_elem

    log_str = f'{site} - list update - {object_name}'
    logger.debug(f'{log_str} - {len(original_elems)} original - {len(data_elems)} data')

    for elem_id, original_elem in original_elems.items():
        if elem_id not in data_elems:
            changes = True
            log(f'{site} - deleting - {object_name} {elem_id}', log=True)
            original_elem.delete()

    for data_id, data_elem in data_elems.items():
        elem_log = f'{object_name} {data_id}'
        elem = related_manager.filter(**{id_property_elem: data_id}).first()
        is_new = elem is None
        if is_new:
            log(f'creating - {elem_log}', log=True)
            elem = new_elem_func()
            setattr(elem, id_property_elem, data_id)
            update_object_from_data(elem, properties_mapping, data_elem)
            try:
                elem.save()
                related_manager.add(elem)
                changes = True
            except IntegrityError:
                # if we get integrity error, fetch and set as existing
                elem = related_manager.filter(**{id_property_elem: data_id}).first()
                if elem:
                    is_new = False
                    log(f'integrity error - updating - {elem_log}', log=True)
                else:
                    # if integrity error AND element does not exist, log error
                    # FIXME: remove all this for django-weebly
                    log('')
                    log('Data:')
                    log('')
                    for line in json.dumps(json_data, indent=4).split('\n'):
                        line = line.replace(' ', '&nbsp;')
                        log(line)
                    log_msg = f'{log_str} - integrity error and not exists - {elem_log}'
                    logger.error(log_msg, exc_info=True)
                    send_email_to_admins(log_msg, '<br/>'.join(log_strs))
                    log_strs = []
                    continue
        save = update_object_from_data(elem, properties_mapping, data_elem)
        if not is_new:
            if save:
                log(f'updating - {elem_log}', log=True)
            else:
                log(f'already exists - {elem_log}')
        if save or is_new:
            changes = True
            elem.save()
    return {'changes': changes}


def update_object_from_data(obj, properties_mapping, data):
    """
    updates the object and returns true if the object must be saved
    Does NOT save
    """
    save = False
    for data_property, obj_property, transform_func in properties_mapping:
        obj_val = getattr(obj, obj_property)
        data_val = data[data_property]
        if transform_func:
            data_val = transform_func(data_val)
        if obj_val != data_val:
            setattr(obj, obj_property, data_val)
            save = True
    return save


def compose(f, g):
    return lambda x: f(g(x))


def none_to_empty(val):
    return val if val else ''


def unescape_func_not_null(val):
    val = none_to_empty(val)
    return unescape_func(val)


def unescape_func(val):
    """
    helper for update funcs
    """
    if not val: return val
    val = val.replace('&amp;amp;', '&')  # Fixing strange weebly double HTML encoding
    return html.unescape(val) if val is not None else None


def url_func(url):
    """
    helper for update funcs for URL values
    """
    if url is None: return None
    if is_absolute(url): return url
    return ('/' if not url.startswith('/') else '') + url


def unescape_dict_val_func(val):
    if not val: return val
    return map_dict(val, lambda tag, name: html.unescape(name))


def valid_email_func(val):
    return val if is_valid_email(val) else None


def find_in_map_list(l, property_name, id_value):
    for elem in l:
        elem_id = int(elem[property_name])
        if elem_id == id_value:
            return elem
    return None
