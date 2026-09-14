# MacroVision real-world nutrition validation

## Purpose

This protocol measures how well MacroVision estimates calories, fat,
carbohydrate, and protein from food photos taken in normal use. It does not use
the training or test dataset. Each test item is a real meal that is prepared,
weighed, photographed with a consumer phone, and then eaten or discarded.

The primary question is:

> If someone photographs the food they are actually about to eat, how far is
> MacroVision's estimate from the best practical measurement of that serving?

This is a product validation study, not a claim of laboratory nutrient
analysis. Ingredient databases and package labels carry their own uncertainty,
so results must identify the source of each ground-truth value.

## Why weighing is required

A photograph alone cannot establish calorie accuracy because the true portion,
hidden ingredients, cooking fat, and recipe are unknown. Internet photographs
without preparation records can test whether inference crashes or recognizes a
food, but they cannot provide defensible calorie-error measurements.

NIST recommends measuring recipe ingredients by mass with an appropriate
kitchen scale rather than by cups or other volume measures. USDA FoodData
Central provides nutrient data for generic foods, analytically characterized
foundation foods, survey foods, and branded products. FDA serving sizes describe
amounts customarily consumed and are not recommended portions; for this study,
the amount actually plated is weighed instead of assumed from the serving size.

Sources:

- [NIST culinary measurement guidance](https://www.nist.gov/pml/owm/metric-si/metric-kitchen/metric-kitchen-culinary-measurement-tips)
- [USDA FoodData Central data documentation](https://fdc.nal.usda.gov/data-documentation/)
- [USDA Foundation Foods documentation](https://fdc.nal.usda.gov/Foundation_Foods_Documentation/)
- [FDA explanation of serving sizes](https://www.fda.gov/consumers/consumer-updates/food-serving-sizes-have-reality-check)

## Study design

### Phase 1: pilot

Run 30 meals to verify the collection process and discover obvious failure
modes. Do not tune the model against these meals and then report the same meals
as final performance.

### Phase 2: locked evaluation

Collect at least 100 new meals after the protocol and model version are frozen.
Use a mixture representative of intended users:

| Group | Suggested share | Examples |
|---|---:|---|
| Simple, visible foods | 20% | eggs and toast, fruit, steak and potatoes |
| Mixed dishes | 25% | curry and rice, pasta, stir-fry, casserole |
| Bowls and salads | 15% | oatmeal, poke bowl, dressed salad |
| Packaged/takeaway | 15% | frozen meal, yogurt, sandwich, pizza |
| Energy-dense or hidden ingredients | 15% | fried food, creamy sauce, added oil, nut butter |
| Difficult presentation | 10% | low light, partial occlusion, similar-coloured foods |

Record cuisine, meal type, container type, food count, whether components
overlap, and whether oil/sauce is visually hidden. These tags are decided before
looking at prediction errors.

Recruit more than one person to prepare and photograph meals. One cook, one
phone, and one kitchen measures repeatability in that environment—not real-world
generalization.

## Equipment

- Consumer kitchen scale readable to 1 g, with tare support.
- One known reference mass used for a quick daily scale check.
- At least two ordinary phone models if available.
- Normal plates, bowls, takeaway containers, and lighting.
- Ingredient packaging and recipe notes kept out of the model photograph.

At the start of each collection day, place the reference mass on the scale three
times. Record the readings. Replace or recalibrate the scale if readings differ
materially from the known mass or do not repeat within 1 g.

## Establishing ground truth

Assign every meal one of the following evidence levels.

### Level A: weighed recipe or weighed individual components

This is the preferred method.

1. Tare the empty preparation container.
2. Weigh every ingredient in grams before adding it. Record brand and whether
   the weight is raw, cooked, drained, or edible portion.
3. Record oils, butter, dressing, sugar, milk, sauces, and toppings. These are
   common sources of invisible calorie error.
4. Match each ingredient to its exact package label or an appropriate USDA
   FoodData Central entry in the same preparation state.
5. Calculate the nutrients of the complete recipe:

   `recipe nutrient = sum(ingredient grams × nutrient per 100 g / 100)`

6. Weigh the completed cooked recipe. This captures water gained or lost during
   cooking without pretending that water changes total recipe calories.
7. Weigh the amount plated from that recipe.
8. Allocate recipe nutrients by plated cooked-mass fraction:

   `serving nutrient = recipe nutrient × plated grams / cooked recipe grams`

For visibly separable meals, weighing each plated component is better than
assuming the serving is a uniform fraction of the whole recipe.

### Level B: packaged food

1. Photograph the food after removing or hiding the label.
2. Weigh the edible amount placed on the plate.
3. Convert the label values using the label's gram serving:

   `actual nutrient = label nutrient × plated grams / label serving grams`

Do not treat “one bowl” or “one serving” as ground truth unless the measured
weight matches it.

### Level C: restaurant-published nutrition

Use only standard, unmodified menu items with published nutrition. Record the
restaurant, exact item, size, purchase date, and published values. Weigh and
photograph the received item when practical. Report Level C separately because
the actual serving may differ from the restaurant's standardized value.

## Photography protocol

The primary photo should reproduce normal product use:

- Use the phone's normal camera mode with no manual editing.
- Photograph the complete serving immediately before eating.
- Use a natural handheld distance and a roughly 35–60 degree view.
- Do not include ingredient packages, nutrition labels, scale displays, recipe
  notes, or filenames that reveal the answer.
- Do not place a ruler or calibration object in the primary photo unless the
  shipped product instructs every user to do so.

For 25% of meals, take repeat photos without changing the food:

1. Normal primary view.
2. Near-overhead view.
3. Lower side view.
4. One naturally difficult view, such as dimmer light or partial occlusion.

These repeats measure sensitivity to photography rather than food composition.
Only the predesignated primary image contributes to the main accuracy score;
repeat views are reported as robustness results.

## Blinding and execution

1. Generate a random `meal_id`; filenames contain only this ID and view number.
2. Store ground truth separately from the inference directory.
3. Freeze the code revision, model weight hashes, configuration, device, API
   model name, and prompt before the locked evaluation.
4. Run every primary image once through both `combined` and `regression` modes.
5. Do not pass ingredient names, weights, labels, or nutrition hints into the
   primary evaluation.
6. Save the raw response, source/fallback field, latency, errors, and timestamp.
7. Join predictions to ground truth only after inference completes.

MacroVision's `combined` path uses a VLM estimate per 100 g and the depth/ViT
regressor as a portion signal. The `regression` path uses the depth/ViT model
alone. They must be scored separately. A combined request that falls back to
regression is counted as a fallback and must not be presented as successful VLM
coverage.

## Data record

Store one row per meal/view/mode with at least:

```text
meal_id, participant_id, capture_date, phone, view, primary_view,
evidence_level, food_group, cuisine, container, mixed_dish, hidden_fat,
ground_truth_source, serving_weight_g,
actual_calories, actual_fat_g, actual_carbs_g, actual_protein_g,
mode, model_version, weights_hash, vlm_model, prediction_source,
predicted_calories, predicted_fat_g, predicted_carbs_g, predicted_protein_g,
latency_ms, error_code, notes
```

Keep identifying participant data out of this file. Preserve original images
and immutable raw prediction responses so calculations can be audited.

## Metrics

Calculate each metric for calories, fat, carbohydrate, and protein, separately
for `combined` and `regression`:

- Mean absolute error (MAE), in kcal or grams.
- Median absolute error, to describe a typical meal without domination by a few
  catastrophic misses.
- Mean signed error (`predicted - actual`) to reveal systematic over- or
  underestimation.
- Median absolute percentage error (MdAPE). Exclude or separately report
  near-zero nutrient values where percentage error is unstable.
- Percentage within ±10%, ±20%, and ±30% of ground truth.
- Failure rate, fallback rate, and median/95th-percentile latency.

Report 95% bootstrap confidence intervals by resampling meals, not individual
views. Views of the same meal are correlated and must not be counted as
independent meals.

Also report metrics by food group, evidence level, calorie range, mixed versus
separate components, hidden-fat status, phone, and photography condition. Do
not publish only an overall average; a model can look acceptable overall while
failing consistently on sauces or mixed dishes.

For repeat-view meals, calculate the range or coefficient of variation across
views. Large differences with unchanged food indicate portion/depth instability.

## Practical release gates

Set thresholds before running the locked evaluation. An initial product gate to
debate—not a validated clinical standard—is:

- At least 90% of valid images return a prediction.
- Median absolute calorie error no greater than 20%.
- At least 70% of meals within ±30% calories.
- No major subgroup with median signed calorie error beyond ±25%.
- Combined mode materially improves calorie error over regression mode.
- Repeat views of the same meal usually remain within 20% of one another.

If the application uses predictions for medical dosing or treatment decisions,
this household protocol is insufficient; that requires domain experts,
laboratory-quality reference methods, and appropriate regulatory validation.

## Failure review

For every meal outside ±30% calorie error, review without changing the locked
score:

- Was the food identity wrong?
- Was serving mass/volume wrong despite correct identity?
- Was hidden oil, dressing, filling, or cooking method unknowable from pixels?
- Did depth fail on a flat plate, reflective container, transparent vessel, or
  overhead view?
- Did combined mode use the VLM or silently fall back?
- Was ground truth itself weak or incorrectly matched to raw/cooked state?

Separate **model-correctable errors** from **information-not-visible errors**.
The latter should influence UX—for example, asking “Was oil added?”—instead of
being treated only as a training failure.

## Honest interpretation

The final report should state the population, phones, cuisines, calorie range,
ground-truth evidence mix, model versions, and confidence intervals. Results
apply to that tested usage distribution. They do not establish universal
accuracy, and nutrition estimates should remain editable by the user.
