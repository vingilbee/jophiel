# Legacy intent-classifier artifacts

This directory belongs to the package-root local intent prototype, not the V1
cognitive teacher or its training projector. See the [current guide](../v0/README.md).

The prototype training utility writes versioned JSON classifier artifacts. Its
runtime loads them lazily and can fall back to deterministic intent rules when
an artifact is absent or invalid. That fallback describes only this prototype;
it is not a provider-failure fallback policy for the model-backed V1 pipeline.
