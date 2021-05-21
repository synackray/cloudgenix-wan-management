# CloudGenix WAN Management

Currently CloudGenix requires that all WAN circuits within a branch have a manually set bandwidth. This tool leverages the PCM data used by CloudGenix to calculate WAN circuit capacity and automatically sets the bandwidth values for each circuit based on X interval (default: 4 hours).

## Usage

```
$ python app.py -h
usage: app.py [-h] -c CLOUDGENIX_TOKEN [-H 4] [-p 95] [-v]

optional arguments:
  -h, --help            show this help message and exit
  -c CLOUDGENIX_TOKEN, --cloudgenix-token CLOUDGENIX_TOKEN
                        The API token for authenticating CloudGenix requests.
  -H 4, --hours 4       Number of hours back from current time to calculate metrics for. (default: 4)
  -p 95, --percentile 95
                        Number of hours back from current time to calculate metrics for. (default: 95)
  -v, --verbose         Enable verbose output. Intended for debugging purposes only.
```