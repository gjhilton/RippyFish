# RippyFish

**Problem:** You've got a web page containing an OpenSeadragon IIIF viewer. BUT what you'd prefer is just a high resolution static image.

**Solution:** This was mostly vibecoded in 5 minutes to solve an urgent need. I recommned you dont use it. Also, you shouold be nice to copyright owners and people who run servers: the chances are they're in the museums, libraries and archives sector and those folsk deserve LOVE, not this sort of thing.

##  Usage

First, install dependencies:

    pip install -r requirements.txt

### Then run the script:

Basic usage (saves to current directory)

    python rippyfish.py <url>

Specify output directory

    python rippyfish.py <url> --output ./my_images

Adjust concurrent downloads (default: 10)

    python rippyfish.py <url> --workers 20

Enable verbose logging

    python rippyfish.py <url> --verbose

## How It Works

1. HTML Parsing: Uses BeautifulSoup to find OpenSeadragon script blocks and extracts the tileSources array using regex
  
2. IIIF Metadata: Fetches each info.json file to determine image
  dimensions, tile sizes, and available zoom levels
  
3. Smart Downloading:
- For smaller images (<2000x2000px), downloads the full image in one
  request
- For larger tiled images, calculates the tile grid and downloads each tile in the proper region

4. Compositing: Uses PIL/Pillow to paste tiles into their correct
  positions in the output image
  
5. Parallel Downloads: Uses ThreadPoolExecutor to download multiple tiles concurrently with configurable worker count
