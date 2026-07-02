"""local-flow: local-first desktop dictation with LM Studio polish.

Pipeline: mic capture -> VAD -> local ASR -> rule clean -> LM Studio polish
-> dictionary/snippets/dictation commands -> text insertion.
Every stage sits behind an adapter interface so it can be mocked in tests.
"""

__version__ = "0.1.0"
