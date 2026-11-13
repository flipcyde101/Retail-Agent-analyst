import os

# PydanticAI prints a start-up banner on the first run; keep terminal and test output clean.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
