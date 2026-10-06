# Intent training data

Store reviewed intent examples as JSON Lines. Each non-empty line must contain exactly:

```json
{"text": "Can you analyze this image?", "intent": "request"}
```

Allowed labels are `question`, `request`, `inform`, `clarify`, `feedback`,
`greeting`, and `unknown`.

The legacy experiment is intentionally not copied automatically: its message,
perception, and label files have different record counts, so their alignment cannot
be verified. Only manually reviewed examples should become training data here.
