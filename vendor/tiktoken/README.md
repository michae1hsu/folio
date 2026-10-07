# Bundled cl100k_base vocabulary

The `cl100k_base` vocabulary is bundled so Folio can initialize its tokenizer
without a network download.

- Upstream file: https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken
- SHA-256: `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`
- Size: 1,681,126 bytes
- Verified against the `cl100k_base` definition in OpenAI tiktoken 0.14.0.
- Upstream project: https://github.com/openai/tiktoken
- Copyright and license notice: [LICENSE](LICENSE).

`backend/tokenizer.py` verifies these exact bytes and seeds the standard tiktoken
cache before loading the existing encoding. A damaged bundle fails locally;
Folio does not download a replacement. Keep this file byte-for-byte unchanged.
