from app.scrapers.base import CaptchaError, ScrapedResult, Scraper
from app.scrapers.bing import BingScraper
from app.scrapers.duckduckgo import DuckDuckGoScraper
from app.scrapers.google import GoogleScraper

SCRAPERS: dict[str, type[Scraper]] = {
    "google": GoogleScraper,
    "bing": BingScraper,
    "duckduckgo": DuckDuckGoScraper,
}

__all__ = ["CaptchaError", "ScrapedResult", "Scraper", "SCRAPERS"]
