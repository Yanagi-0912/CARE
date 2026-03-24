class RagNoHitsError(Exception):
    """檢索無可用片段時拋出；由 orchestration 決定後續（例如帶 tools 的 Gemini fallback）。"""
