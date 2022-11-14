# django-weebly
Django app for running Weebly apps

## Setup

### Settings

```
INSTALLED_APPS = [
    ...
    'weebly',
    ...
]
```

And

```
MIDDLEWARE = [
    ...
    'weebly.views.WeeblyAuthMiddleware',
    ...
]

```
### Settings variables:

```
WEEBLY_APP_NAME = "..."
WEEBLY_SECRET = "..."
WEEBLY_CLIENT_ID = "..."
WEEBLY_CARD_NAME = "..."
DEFAULT_WEEBLY_AUTH = ...
```

`DEFAULT_WEEBLY_AUTH` is the database id for the default `WeeblyAuth` for notifying payments. Sometimes there are payments that need to be reported but the user already uninstalled the app, so their `WeeblyAuth` is no longer valid. This usually happens with refunds.

### Urls

Add this to handle Weebly's OAuth:

```
urlpatterns = [
    ...
    path('oauth', weebly_oauth),
    ...
]
```
