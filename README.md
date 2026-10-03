
# MD-base

UNFINISHED!

This service contains simple python scripts for text and JSON.


Origin of stop word lists:
https://github.com/stopwords-iso/stopwords-iso

## API

Endpoint: `http://localhost:9008/process`

Service registration endpoints:

- `GET /health` returns basic liveness status.
- `GET /config` returns runtime descriptor from `service.json`.
- `GET /help` returns markdown help (from `index.md`, `help/index.md`, or `README.md`).

### File storage mode (default for `elg_fs`)

- Send multipart field `message` containing the queue message JSON.
- Service reads input directly from `request.file.path` (relative to `MD_PATH`).
- Service writes output files to `data/<db>/tmp`.
- Service returns `response.type = "disk"` with `response.files[]` where `path` is filename only.
- Adapter (`elg_fs`) sends one callback per file to `/api/nomad/process/files/tmp`.

Important:
- When `STORAGE_MODE=disk` (or `FILE_STORAGE_MODE=disk`), `MD_PATH` must be set.
- Service loads variables from `.env` automatically at startup.

Recommended `.env`:

	MD_PATH="/home/YOUR_USERNAME/Projects/MessyDesk"
	STORAGE_MODE="disk"

### Legacy compatibility mode

- Service still accepts multipart fields `message` + `content`.
- This keeps older direct HTTP testing workflows working.

## Disk response example

```json
{
	"task": "split_text",
	"response": {
		"type": "disk",
		"files": [
			{
				"path": "sample_1.txt",
				"label": "sample_1.txt",
				"type": "text",
				"extension": "txt"
			}
		]
	}
}
```

## Running as service (locally)

Start from project directory so local `.env` is picked up:

	python api.py


Or build container and start it (will also pick .env)

	make build
	make start


## Starting service for MessyDesk (local development)

in MD-consumers 

	TOPIC=md-text-base_fs DEV_URL=http://localhost:9008 node src/index.mjs



### Example API call 

Run these from MD-text-base directory:



	curl -X POST -H "Content-Type: multipart/form-data" \
	  -F "message=@test/stopwords_en.json;type=application/json" \
	  -F "content=@test/sample.txt;type=text/plain" \
	  http://localhost:9008/process





