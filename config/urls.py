from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path

from .views import home

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
]

# Раздача загруженных файлов (фактура) при локальной разработке;
# на сервере этим займётся Caddy.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
