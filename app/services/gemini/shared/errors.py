class GeminiError(Exception):
    pass


class GeminiNetworkError(GeminiError):
    pass


class GeminiHttpError(GeminiError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class GeminiSchemaError(GeminiError):
    pass


class GeminiParseError(GeminiError):
    pass


class GeminiUnknownError(GeminiError):
    pass
