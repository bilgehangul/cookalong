# Recipe authoring spec

You turn one cooking video transcript into one structured recipe JSON object.

Input: `corpus/<VIDEO_ID>.txt` — header fields then a transcript where each line
is `[seconds] text`. Those bracketed numbers are real playback timestamps.

Output: one `.json` file per video containing a single JSON object.

## Write in your own words — this matters

The transcript is someone else's copyrighted speech. **Never copy its phrasing
into your output.** Ingredient names, quantities, temperatures and times are
plain facts and should be carried over exactly. Everything you write in `text`,
`narration`, `how` and `watch_for` must be your own original prose describing
the same action. Do not paraphrase sentence-by-sentence — read what happens,
then describe it yourself, plainly.

## Timestamps must be real

Every `start_seconds` must be a timestamp that actually appears in the
transcript, at the moment that action begins. Never invent or interpolate one.
Steps must be in strictly increasing `start_seconds` order.

## Cover the whole video — no step cap

- There is **no maximum number of steps.** Roughly one step per 20–30 seconds
  of instructional content: a 6-minute video is usually 12–18 steps, a
  10-minute one 20–30.
- Work forward to the **last line that still gives an instruction**, and only
  then stop. Storage, refrigeration, freezing, reheating and serving directions
  are real steps — include them.
- Skip only outros, sponsor reads, channel/app plugs, merch talk, and container
  or equipment opinions that carry no instruction. Their presence near the end
  is never a reason to stop early.
- `first_step_at` should be under 40% of duration and `last_step_at` above 70%
  of the instructional content. If your last step lands early, you stopped too
  soon — go back and read the rest.

## Schema — exact, no extra keys

```json
{
  "video_id": "...",
  "title": "Short dish name you write yourself, not the video's clickbait title",
  "channel": { "name": "<CHANNEL>", "link": "<CHANNEL_URL>" },
  "thumbnail": "https://i.ytimg.com/vi/<VIDEO_ID>/maxresdefault.jpg",
  "duration_seconds": <DURATION_SECONDS from the header, integer>,
  "tags": ["Main", "Mexican"],
  "servings": 5,
  "total_time_minutes": 40,
  "ingredients": [
    { "name": "ground beef, 90/10", "quantity": "2", "unit": "lb (908 g)",
      "normalized": "ground beef", "category": "Protein" }
  ],
  "steps": [
    { "index": 1, "text": "Imperative sentence, one action.", "start_seconds": 17,
      "end_seconds": 45, "narration": "Same step, phrased to be read aloud.",
      "detail": { "how": "2-4 sentences of concrete mechanics.",
                  "watch_for": "The common mistake or failure signal.",
                  "duration_hint": "Rough time, or null" },
      "terms": [ { "term": "julienne", "start_seconds": 145 } ] }
  ],
  "coverage": { "first_step_at": 17, "last_step_at": 333, "steps": 20 }
}
```

### Field rules

- `index` — 1-based, sequential, no gaps.
- `end_seconds` — the **next** step's `start_seconds`; for the final step, the
  video's `duration_seconds`.
- `detail` — an object for any step involving a technique, a judgement call
  ("until golden"), or something a beginner gets wrong. **`null` for trivial
  steps.** Do not pad; roughly half of all steps having detail is healthy.
  `duration_hint` may be `null` inside an otherwise present detail object.
- `terms` — technique words that **literally appear in that step's `text`**
  (case-insensitive whole-word match), each with a real transcript timestamp.
  If the word is not in your `text`, do not list it — the UI matches on the text
  and a mismatch renders nothing. Empty array is fine and common.
- `title` — name the dish, e.g. "Teriyaki Chicken Bowls". Strip the video's
  clickbait ("Why Can't I Stop Eating This...").

### Fixed vocabularies — reject anything else

- **Course** (at least one, required): Breakfast, Appetizer, Salad, Soup, Main,
  Side, Dessert, Snack, Drink, Sauce
- **Cuisine** (at most one): Italian, Mexican, Chinese, Japanese, Indian, Thai,
  Mediterranean, American, French, Korean, Middle Eastern, Other
- **Attributes** (any number): Vegetarian, Vegan, Gluten-Free, Quick, One-Pot,
  Baked, Grilled, No-Cook

`category` for every ingredient is exactly one of: Produce, Protein, Dairy,
Pantry, Spices, Other.

### `normalized` — match the pantry catalogue where you can

Use one of these exact strings when the ingredient is one of them, so the
pantry check recognises it. Otherwise use a short lowercase generic name.

apple, avocado, bacon, baking powder, baking soda, balsamic vinegar, banana,
basil, bay leaves, bell pepper, black beans, black pepper, bread, breadcrumbs,
broccoli, brown sugar, butter, cabbage, canned tomatoes, canned tuna, carrot,
cauliflower, cayenne, celery, cheddar, cherry tomatoes, chicken breast,
chicken stock, chicken thighs, chickpeas, chili flakes, chili powder, cilantro,
cinnamon, coconut milk, coriander, corn, cornstarch, cream cheese, cucumber,
cumin, curry powder, eggplant, eggs, feta, fish sauce, flour, garlic,
garlic powder, ginger, greek yogurt, green beans, ground beef, heavy cream,
honey, hot sauce, italian seasoning, jalapeno, kale, ketchup, kidney beans,
lemon, lentils, lettuce, lime, maple syrup, mayonnaise, milk, mint, mozzarella,
mushrooms, mustard, noodles, nutmeg, oats, olive oil, onion, onion powder,
oregano, oyster sauce, paprika, parmesan, parsley, pasta, peanut butter, peas,
pork, potato, quinoa, red onion, rice, rice vinegar, rosemary, salmon, salt,
sausage, scallions, sesame oil, sesame seeds, shallot, shrimp, smoked paprika,
sour cream, soy sauce, spinach, sriracha, steak, sugar, sweet potato, thyme,
tofu, tomato, tomato paste, tortillas, turmeric, vanilla extract,
vegetable oil, vinegar, white fish, yogurt, zucchini

## Plain-text only

Write ASCII. No smart quotes, em dashes, or degree symbols — write `400F / 204C`
and `1/2 cup`. The file must be valid JSON parseable by `json.loads`.

## Before you finish — verify, do not assume

Run this against each file you write and fix anything it reports:

```
python verify_recipe.py <yourfile>.json
```

It checks the schema, timestamp monotonicity, index/end_seconds chaining,
vocabularies, term-in-text matching, and coverage. Do not report success until
it prints OK for every file you were assigned.
