from __future__ import annotations

import asyncio
from pathlib import Path

import edge_tts


async def _synthesize(text: str, voice: str, output_path: Path) -> None:
    communicator = edge_tts.Communicate(text=text, voice=voice)
    await communicator.save(str(output_path))


def synthesize_to_file(text: str, voice: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(_synthesize(text=text, voice=voice, output_path=output_path))
