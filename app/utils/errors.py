from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ExternalApiError(AppError):
    def __init__(self, code: str, message: str):
        super().__init__(code, message)


class SlackApiError(AppError):
    def __init__(self, message: str):
        super().__init__("SlackApiError", message)


class PubMedNoResultsError(AppError):
    def __init__(self, message: str):
        super().__init__("PubMedNoResults", message)


class PubMedTooManyResultsError(AppError):
    def __init__(self, message: str):
        super().__init__("PubMedTooManyResults", message)


class OpenAIError(AppError):
    def __init__(self, message: str):
        super().__init__("OpenAIError", message)


class WordPressError(AppError):
    def __init__(self, message: str):
        super().__init__("WordPressError", message)
