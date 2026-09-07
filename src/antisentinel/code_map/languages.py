"""Single source of truth for supported Code Map languages."""
LANGUAGE_BY_EXTENSION = {
    '.py':'python', '.go':'go', '.ts':'typescript', '.tsx':'tsx', '.java':'java',
    '.kt':'kotlin', '.rs':'rust', '.cpp':'cpp', '.cc':'cpp', '.h':'cpp',
}
SUPPORTED_EXTENSIONS = frozenset(LANGUAGE_BY_EXTENSION)
def language_for_path(path: str):
    return next((lang for ext, lang in LANGUAGE_BY_EXTENSION.items() if path.endswith(ext)), None)
def is_supported_path(path: str) -> bool: return language_for_path(path) is not None
