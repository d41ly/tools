"""Country (ISO-3166-1 alpha-2) → locale/timezone/language hints for SERPs."""

from __future__ import annotations

COUNTRY_DATA: dict[str, dict[str, str]] = {
    "US": {"locale": "en-US", "timezone": "America/New_York", "lang": "en", "ddg": "us-en"},
    "GB": {"locale": "en-GB", "timezone": "Europe/London", "lang": "en", "ddg": "uk-en"},
    "CA": {"locale": "en-CA", "timezone": "America/Toronto", "lang": "en", "ddg": "ca-en"},
    "AU": {"locale": "en-AU", "timezone": "Australia/Sydney", "lang": "en", "ddg": "au-en"},
    "DE": {"locale": "de-DE", "timezone": "Europe/Berlin", "lang": "de", "ddg": "de-de"},
    "FR": {"locale": "fr-FR", "timezone": "Europe/Paris", "lang": "fr", "ddg": "fr-fr"},
    "ES": {"locale": "es-ES", "timezone": "Europe/Madrid", "lang": "es", "ddg": "es-es"},
    "IT": {"locale": "it-IT", "timezone": "Europe/Rome", "lang": "it", "ddg": "it-it"},
    "NL": {"locale": "nl-NL", "timezone": "Europe/Amsterdam", "lang": "nl", "ddg": "nl-nl"},
    "SE": {"locale": "sv-SE", "timezone": "Europe/Stockholm", "lang": "sv", "ddg": "se-sv"},
    "NO": {"locale": "nb-NO", "timezone": "Europe/Oslo", "lang": "no", "ddg": "no-no"},
    "FI": {"locale": "fi-FI", "timezone": "Europe/Helsinki", "lang": "fi", "ddg": "fi-fi"},
    "DK": {"locale": "da-DK", "timezone": "Europe/Copenhagen", "lang": "da", "ddg": "dk-da"},
    "PL": {"locale": "pl-PL", "timezone": "Europe/Warsaw", "lang": "pl", "ddg": "pl-pl"},
    "PT": {"locale": "pt-PT", "timezone": "Europe/Lisbon", "lang": "pt", "ddg": "pt-pt"},
    "BR": {"locale": "pt-BR", "timezone": "America/Sao_Paulo", "lang": "pt", "ddg": "br-pt"},
    "MX": {"locale": "es-MX", "timezone": "America/Mexico_City", "lang": "es", "ddg": "mx-es"},
    "AR": {"locale": "es-AR", "timezone": "America/Argentina/Buenos_Aires", "lang": "es", "ddg": "ar-es"},
    "JP": {"locale": "ja-JP", "timezone": "Asia/Tokyo", "lang": "ja", "ddg": "jp-jp"},
    "CN": {"locale": "zh-CN", "timezone": "Asia/Shanghai", "lang": "zh-CN", "ddg": "cn-zh"},
    "KR": {"locale": "ko-KR", "timezone": "Asia/Seoul", "lang": "ko", "ddg": "kr-kr"},
    "IN": {"locale": "en-IN", "timezone": "Asia/Kolkata", "lang": "en", "ddg": "in-en"},
    "SG": {"locale": "en-SG", "timezone": "Asia/Singapore", "lang": "en", "ddg": "sg-en"},
    "HK": {"locale": "zh-HK", "timezone": "Asia/Hong_Kong", "lang": "zh-HK", "ddg": "hk-tzh"},
    "TW": {"locale": "zh-TW", "timezone": "Asia/Taipei", "lang": "zh-TW", "ddg": "tw-tzh"},
    "RU": {"locale": "ru-RU", "timezone": "Europe/Moscow", "lang": "ru", "ddg": "ru-ru"},
    "TR": {"locale": "tr-TR", "timezone": "Europe/Istanbul", "lang": "tr", "ddg": "tr-tr"},
    "AE": {"locale": "ar-AE", "timezone": "Asia/Dubai", "lang": "ar", "ddg": "xa-ar"},
    "SA": {"locale": "ar-SA", "timezone": "Asia/Riyadh", "lang": "ar", "ddg": "xa-ar"},
    "ZA": {"locale": "en-ZA", "timezone": "Africa/Johannesburg", "lang": "en", "ddg": "za-en"},
    "EG": {"locale": "ar-EG", "timezone": "Africa/Cairo", "lang": "ar", "ddg": "xa-ar"},
    "NG": {"locale": "en-NG", "timezone": "Africa/Lagos", "lang": "en", "ddg": "ng-en"},
    "IE": {"locale": "en-IE", "timezone": "Europe/Dublin", "lang": "en", "ddg": "ie-en"},
    "BE": {"locale": "nl-BE", "timezone": "Europe/Brussels", "lang": "nl", "ddg": "be-nl"},
    "CH": {"locale": "de-CH", "timezone": "Europe/Zurich", "lang": "de", "ddg": "ch-de"},
    "AT": {"locale": "de-AT", "timezone": "Europe/Vienna", "lang": "de", "ddg": "at-de"},
    "GR": {"locale": "el-GR", "timezone": "Europe/Athens", "lang": "el", "ddg": "gr-el"},
    "CZ": {"locale": "cs-CZ", "timezone": "Europe/Prague", "lang": "cs", "ddg": "cz-cs"},
    "HU": {"locale": "hu-HU", "timezone": "Europe/Budapest", "lang": "hu", "ddg": "hu-hu"},
    "RO": {"locale": "ro-RO", "timezone": "Europe/Bucharest", "lang": "ro", "ddg": "ro-ro"},
    "UA": {"locale": "uk-UA", "timezone": "Europe/Kyiv", "lang": "uk", "ddg": "ua-uk"},
    "IL": {"locale": "he-IL", "timezone": "Asia/Jerusalem", "lang": "he", "ddg": "il-he"},
    "TH": {"locale": "th-TH", "timezone": "Asia/Bangkok", "lang": "th", "ddg": "th-th"},
    "VN": {"locale": "vi-VN", "timezone": "Asia/Ho_Chi_Minh", "lang": "vi", "ddg": "vn-vi"},
    "ID": {"locale": "id-ID", "timezone": "Asia/Jakarta", "lang": "id", "ddg": "id-en"},
    "MY": {"locale": "ms-MY", "timezone": "Asia/Kuala_Lumpur", "lang": "ms", "ddg": "my-en"},
    "PH": {"locale": "en-PH", "timezone": "Asia/Manila", "lang": "en", "ddg": "ph-en"},
    "NZ": {"locale": "en-NZ", "timezone": "Pacific/Auckland", "lang": "en", "ddg": "nz-en"},
    "CL": {"locale": "es-CL", "timezone": "America/Santiago", "lang": "es", "ddg": "cl-es"},
    "CO": {"locale": "es-CO", "timezone": "America/Bogota", "lang": "es", "ddg": "co-es"},
}


def info(country: str) -> dict[str, str]:
    return COUNTRY_DATA.get(country.upper(), COUNTRY_DATA["US"])


def countries() -> list[dict[str, str]]:
    return [{"code": c, "locale": d["locale"]} for c, d in sorted(COUNTRY_DATA.items())]
