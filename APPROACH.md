# Label Verification Prototype: Approach, Tools and Assumptions

## Summary

Label Checker compares an alcohol label image with its application data (brand name, class/type, alcohol content, net contents) and shows Pass, Needs review or Fail for each field, plus the government warning. It reads the label fully offline with Tesseract OCR, so nothing leaves the machine. It checks one label at a time or a batch of up to 500.

- Source code: https://github.com/Hiten1810/label-verification
- Live demo: https://label-verification.onrender.com (free host: about 4 seconds per label, and up to a minute to wake after sitting idle)
- Setup and run instructions: see [README.md](README.md)

## Approach

Each design choice answers something a stakeholder said in the brief.

| What the brief said | What I did |
| --- | --- |
| Over about 5 seconds, nobody will use it | OCR runs locally on a single thread, with a warm-up at startup. A label takes 0.2 to 1 second on a normal machine and about 3 to 5 seconds on the free demo host. |
| The network blocks outbound traffic | Tesseract runs as a local process. There are no cloud APIs and no web fonts or scripts. I confirmed it works with Wi-Fi turned off. |
| Agents range from very comfortable to not at all with technology | One page with numbered steps and large type. Every result uses colour, an icon and words, and errors are written in plain English. |
| "STONE'S THROW" vs "Stone's Throw" takes judgment | Three outcomes, not two. A difference only in capitalization or punctuation is Needs review, not Fail, so the agent makes the call. |
| The warning must be exact, in capitals and bold | Word-for-word comparison with the statutory text. The heading must be capitalized with a colon. Bold cannot be read from OCR text, so the page reminds the agent to confirm it. |
| Labels may be photographed at odd angles or in poor light | Rotation flag, grayscale, contrast boost, downscaling of very large images, and straightening of tilts up to about 15 degrees. |
| Importers send 200 to 300 labels at once | A Multiple labels (Batch) tab: upload a CSV and a ZIP of images, watch progress, see problems first, and download the results as a CSV. |

### How one label is checked

1. Clean up the image (the steps above).
2. Read the text with Tesseract, offline.
3. Compare each field with the application using the rules below.
4. The overall result is the worst field result.

| Field | Pass | Needs review | Fail |
| --- | --- | --- | --- |
| Brand name, class/type | Same words, whole words only | Only capitalization or punctuation differs, a near match that looks like an OCR slip, or the label line has extra words (for example DISTILLERY missing from the application) | Different text. The closest text found on the label is shown. |
| Alcohol content | Same percentage, in any common wording (45% Alc./Vol., ABV) | A printed proof that is not twice the percentage | Different percentage, or none found |
| Net contents | Same volume after converting mL, cL, L and fl oz (1% tolerance) | None | Different volume, or none found |
| Government warning | Exact statutory wording, with GOVERNMENT WARNING: in capitals and a colon | One character off, which is probably an OCR slip | Reworded, shortened, title case, missing colon, or absent |

Batch checking runs in a background thread. Several labels are read at once, one failed row never stops the others, and the uploaded images are deleted as soon as the batch finishes.

## Tools used

| Tool | Used for | Why |
| --- | --- | --- |
| Python 3.12 | All application code | Quick to build, with strong image and text libraries |
| FastAPI and uvicorn | Web server and API | Small, validates input, easy to test |
| Tesseract OCR (pytesseract) | Reading label text | Free and runs fully offline. Cloud OCR or a hosted vision model would break the no-outbound-traffic rule. |
| Pillow | Image cleanup and straightening | Pure Python, so no heavy dependencies such as OpenCV |
| RapidFuzz | Near-match comparison | Fast fuzzy matching to separate OCR slips from real differences |
| Plain HTML, CSS and JavaScript | Front end | No build step, no frameworks, no outside requests |
| pytest and httpx | 75 automated tests | Fast checks of every matching rule, the API and batch upload |
| Docker | Packaging | Installs Tesseract into the image, so the host runs exactly what I tested |
| Git and GitHub | Source control | Required deliverable |
| Render | Live demo | Builds from the repository's Dockerfile and gives a public HTTPS address on a free tier |

## Assumptions

- The application data is typed in or supplied in a CSV. There is no connection to COLA, which the IT notes say is out of scope.
- One label image per check, with English text and standard units. The sample label is a distilled spirit, and that is what I built and tested against.
- Only the four fields in the sample plus the government warning are checked. Bottler name and address and country of origin are not verified.
- Wine and beer rules, such as alcohol-content exemptions and different statements, are not modeled.
- Alcohol content is entered as a number such as 45. If a proof is printed, it must equal twice that number.
- Needs review is a prompt for a human decision, not a rejection. The final call stays with the agent.
- The warning wording comes from the statute (27 CFR 16.21).
- It is a prototype. There is no login, nothing is stored long term, and uploaded images are deleted after processing. Batch results stay in server memory for up to an hour.

## Limitations and trade-offs

- **Bold type** in the warning heading cannot be verified from OCR text. A person has to confirm it.
- **Glare** over the text can defeat OCR. The app then fails or asks for review rather than guessing.
- **Tesseract's dictionary** can silently correct a real misspelling on a label, so a typo on the label can pass.
- **Extra text on the same line** as a field can produce a Needs review where a person would say Pass.
- **Accuracy on hard photos** is lower than a cloud OCR service would give. That is the cost of staying offline.
- **Free demo host**: Render's free tier gives the app only 0.1 of a CPU and 512 MB of memory, so a label takes about 4 seconds, the 10-label demo batch takes about 30 to 40 seconds, and a 300-label batch takes 15 to 20 minutes. It also sleeps when idle. On a normal machine it is roughly 15 to 20 times faster.
- **Batch results** live in memory on one server process, so a restart loses them. A production version would use a job queue and storage.
- **Not built**: login, a saved history of results, and checks for bottler address and country of origin.

## Testing and AI assistance

- **75 automated tests** cover the matching rules for every field, the API and its error messages, image straightening and batch upload. Some run real OCR on generated labels.
- **Sample labels** include a good one, ones with a single deliberate mistake, and poor photos (tilted, blurred, low contrast, heavily compressed, very large, glare). I checked the results by hand in the browser.
- **Offline check**: with Wi-Fi turned off, both the one-label page and the batch page work normally.
- **Fresh clone**: the repository was cloned into an empty folder, set up from the requirements file, and passed all tests.
- **AI assistance**: I built this with the help of an AI coding assistant (Claude) for design, code and tests. I ran, tested and reviewed the result myself.