"""Pydantic-модели Wikimedia Commons (Phase 9.1).

Минимальный набор полей, достаточный для:

* отрисовки изображения с обязательной атрибуцией;
* идемпотентного дедупа на стороне importer'а (по ``page_url``);
* записи license-trail в provenance.

Все модели — frozen, с поддержкой extra fields ignored. Это даёт
forward-compat: Commons добавляет новые поля в ``imageinfo``/``extmetadata``
со временем; мы их просто игнорируем, пока не понадобятся.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class _Frozen(BaseModel):
    """Базовый Pydantic-конфиг: immutable, allow extra (forward-compat)."""

    model_config = ConfigDict(frozen=True, extra="ignore", str_strip_whitespace=True)


class License(_Frozen):
    """License metadata (из ``imageinfo.extmetadata``).

    Attributes:
        short_name: Короткое имя license'а (``CC BY-SA 4.0``, ``Public domain``).
            Это то, что мы показываем пользователю в UI.
        url: Каноническая ссылка на текст license'а (``https://creativecommons.org/...``).
            Может отсутствовать для public domain или нестандартных license'ов.
    """

    short_name: str = Field(min_length=1)
    url: HttpUrl | None = None


class Attribution(_Frozen):
    """Attribution-блок (из ``imageinfo.extmetadata``).

    Attributes:
        credit_html: HTML-строка с автором/источником, как её определил
            uploader на Commons. Может содержать ``<a href>``-теги.
            **Рендерим как HTML в UI** (с sanitisation на caller-уровне),
            не как plain text — иначе теряем нужные ссылки на авторов.
        required: Обязательность атрибуции (``true`` для большинства CC-license'ов,
            ``false`` для public domain). Если ``false``, всё равно рекомендуется
            показывать credit для прозрачности.
    """

    credit_html: str | None = None
    required: bool = True


class CommonsImage(_Frozen):
    """Изображение в Wikimedia Commons.

    Attributes:
        title: ``File:Foo.jpg`` (как в Commons).
        page_url: Caнoнический URL страницы файла на Commons
            (``https://commons.wikimedia.org/wiki/File:Foo.jpg``).
            **Используется как ключ дедупа** — стабилен при rename'ах
            через redirect, чего нет у raw image_url'а.
        image_url: Прямой URL на full-size файл (Commons CDN).
        thumb_url: Прямой URL на thumbnail (если запрошено),
            ``None`` если caller не запросил.
        width / height: Размеры в пикселях (full-size).
        mime: ``image/jpeg``, ``image/png``, ``image/svg+xml``…
        license: License metadata. ``None`` если Commons не вернул
            ``LicenseShortName`` — таких файлов почти нет, но защищаемся.
        attribution: Attribution metadata.
    """

    title: str
    page_url: HttpUrl
    image_url: HttpUrl
    thumb_url: HttpUrl | None = None
    width: int | None = None
    height: int | None = None
    mime: str | None = None
    license: License | None = None
    attribution: Attribution = Field(default_factory=Attribution)
