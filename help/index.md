# MD-Text-Base Help

The **MD-Text-Base** service provides essential utilities for manipulating raw text documents and JSON files within your MessyDesk workspace.

---

## Text Tasks

### Search and replace (`search_replace`)
Replaces text with other text, for example to fix recurring OCR errors (`ſ:s`), expand
abbreviations or remove headers.

* **Parameters:**
  * `search_replace` *(text)*: one `search:replace` pair per line. The pairs are applied from top
    to bottom, so a later pair sees the result of the earlier ones. Leave the replacement empty to
    delete the text (`[illegible]:`).
  * A colon that is part of the text is written `\:`, for example `10\:30:half past ten` replaces
    *10:30* with *half past ten*; a backslash is written `\\`.
* Matching is exact: case, spaces and punctuation must match.

### Remove stop words (`remove_stop_words`)
Removes the most common words of a language (*and, the, ja, och*) and keeps the rest, line by line.
Useful before word clouds and word counts.

* **Parameters:**
  * `language` *(English, Finnish or Swedish; default English)*.

### Create wordcloud (`wordcloud`)
Draws the most frequent words of the text as a picture (PNG), bigger the more often they occur.
Remove stop words first to see the words that matter.

---

## Split Tasks

Split crunchers are one-to-many operations that break a single long text file down into a structured set of smaller, more manageable text nodes. This is highly useful for tools like the **Search** service, which query and index data on a per-document level.

### Split Text (`split_text`)
Breaks a text document into uniform chunks based on a maximum character threshold.

* **Parameters:**
  * `chunk_size` *(integer, default: `1000`)*: The maximum number of characters allowed in each chunk (including spaces).
  * `trim` *(boolean, default: `false`)*: When enabled, collapses repeated whitespaces, tabs, and consecutive newlines before measuring the character count.

### Split by Character Sequence (`split_by_character_sequence`)
Allows you to define exact structural boundaries or anchor markers where a text document should be cut into separate chunks. This is ideal for dividing a single file containing a collection of letters, ledger entries, or survey responses.

* **Parameters:**
  * `split_sequence` *(string, required)*: The specific text token or delimiter string that marks the beginning of a new document chunk (e.g., `### NEW CHAPTER` or `[PAGE BREAK]`).
  * `remove_sequence` *(boolean, default: `false`)*: Strips the matching delimiter token away from the beginning of the newly generated chunks.
  * `sequence_at_line_start` *(boolean, default: `false`)*: Restricts matches to instances where the sequence appears strictly at the start of a line (ignoring leading whitespace).

---

## Join Tasks

Join crunchers are many-to-one or complex grouping utilities designed to merge multiple independent text files into consolidated output documents.

### Clever text join (`join_text`)
Combines multiple files within a set back into unified documents by automatically reading their historical lineage. 

Instead of merging everything into a single massive file, MessyDesk traces the graph's history to locate the original multi-page sources or archives from which the text segments were derived. It then groups and joins the segments accordingly.

> 💡 **How it works:** Imagine you have two distinct multi-page files: `DocumentA.pdf` and `DocumentB.pdf`. You split them into individual pages, run an OCR text extraction cruncher on the pages, and feed all the resulting text segments into a single set. 
> 
> Applying a **Clever Join** to that set evaluates the origin tree: it identifies which segments belong to `DocumentA` and which belong to `DocumentB`, outputting exactly **two** compiled text documents—one matching the complete text of `DocumentA` and one for `DocumentB`. Each output is named after its source, here `DocumentA.txt` and `DocumentB.txt`.

### Raw text join (`join_raw`)
A straightforward consolidation utility. It ignores file lineage entirely, takes every text file present in the connected input set, and dumps their contents back-to-back into a single, unified text node. For now the output file is named by a code (a hash), not after the set.

---

## JSON Tasks

### OCR JSON to text (`json2text`)
Extracts the recognised text lines of an OCR JSON file (`ocr.json`) into a plain text file, one line per text region.

### JSON to CSV (`json2csv`)
Flattens a JSON or JSONL file into a CSV file. Nested keys become dotted column names, lists are joined with `; `, and values are separated with `;`.
