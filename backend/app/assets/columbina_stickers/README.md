# Columbina sticker assets

These 24 PNGs were cropped from `../columbina_sticker.png`, which remains unchanged. They are arranged by the original 6×4 sheet order and named by the visible mood/action:

`confused`, `question`, `love`, `thinking`, `good-job`, `sleepy`, `yum`, `tea`, `hmm`, `shy`, `sorry`, `idea`, `encouragement`, `silent`, `scribble`, `grateful`, `surprised`, `peeking`, `embarrassed`, `praying`, `determined`, `hungry`, and `see-you`.

## Recommended chatbot integration

Use a stable token in assistant output, for example `[[sticker:good-job]]`, and let the frontend render that token as an image. Keep the sticker catalog and usage rules in the agent prompt so the model chooses only from approved names. Do not expose the backend filesystem directly; serve approved sticker files through a dedicated read-only route or copy the approved files into the frontend’s public asset bundle.
