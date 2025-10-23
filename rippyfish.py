#!/usr/bin/env python3
"""
IIIF Image Downloader from OpenSeadragon Embeds

Downloads full-resolution images from IIIF manifests embedded in OpenSeadragon viewers.
"""

import argparse
import json
import logging
import math
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image
from tqdm import tqdm


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class IIIFImageDownloader:
    """Downloads and composites IIIF tiled images."""

    def __init__(self, output_dir: str = ".", max_workers: int = 10):
        """
        Initialize the downloader.

        Args:
            output_dir: Directory to save output images
            max_workers: Maximum number of concurrent download threads
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.max_workers = max_workers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'IIIF-Image-Downloader/1.0'
        })

    def fetch_page(self, url: str) -> str:
        """
        Fetch the HTML content of a webpage.

        Args:
            url: URL of the webpage

        Returns:
            HTML content as string
        """
        logger.info(f"Fetching page: {url}")
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    def extract_openseadragon_sources(self, html: str) -> List[str]:
        """
        Extract IIIF manifest URLs from OpenSeadragon configuration in HTML.

        Args:
            html: HTML content

        Returns:
            List of IIIF manifest URLs
        """
        logger.info("Extracting OpenSeadragon tileSources...")

        # Parse HTML
        soup = BeautifulSoup(html, 'html.parser')

        # Find all script tags
        script_tags = soup.find_all('script', type='text/javascript')

        tile_sources = []

        for script in script_tags:
            script_content = script.string
            if script_content and 'OpenSeadragon' in script_content:
                # Look for tileSources array
                # Pattern to match tileSources: [...] including multiline
                pattern = r'tileSources\s*:\s*\[(.*?)\]'
                match = re.search(pattern, script_content, re.DOTALL)

                if match:
                    sources_str = match.group(1)
                    # Extract URLs (strings in quotes)
                    url_pattern = r'["\']([^"\']+)["\']'
                    urls = re.findall(url_pattern, sources_str)

                    # Filter for IIIF URLs (containing info.json)
                    iiif_urls = [url for url in urls if 'info.json' in url]
                    tile_sources.extend(iiif_urls)

        logger.info(f"Found {len(tile_sources)} IIIF manifest(s)")
        return tile_sources

    def fetch_iiif_metadata(self, info_url: str) -> Dict:
        """
        Fetch IIIF image metadata.

        Args:
            info_url: URL to the IIIF info.json

        Returns:
            Dictionary containing IIIF metadata
        """
        logger.debug(f"Fetching IIIF metadata: {info_url}")
        response = self.session.get(info_url, timeout=30)
        response.raise_for_status()
        return response.json()

    def calculate_tile_grid(self, width: int, height: int, tile_size: int) -> Tuple[int, int]:
        """
        Calculate the number of tiles needed in each dimension.

        Args:
            width: Image width
            height: Image height
            tile_size: Size of each tile

        Returns:
            Tuple of (num_tiles_x, num_tiles_y)
        """
        num_tiles_x = math.ceil(width / tile_size)
        num_tiles_y = math.ceil(height / tile_size)
        return num_tiles_x, num_tiles_y

    def download_tile(self, url: str) -> Optional[Image.Image]:
        """
        Download a single tile.

        Args:
            url: URL of the tile

        Returns:
            PIL Image object or None if download fails
        """
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            from io import BytesIO
            return Image.open(BytesIO(response.content))
        except Exception as e:
            logger.warning(f"Failed to download tile {url}: {e}")
            return None

    def download_and_composite_image(self, info_url: str, output_filename: str) -> bool:
        """
        Download all tiles for an image and composite them.

        Args:
            info_url: IIIF info.json URL
            output_filename: Output filename for the composited image

        Returns:
            True if successful, False otherwise
        """
        try:
            # Fetch metadata
            metadata = self.fetch_iiif_metadata(info_url)

            # Get image dimensions
            width = metadata['width']
            height = metadata['height']

            # Get tile size (default to 256 if not specified)
            tile_size = metadata.get('tiles', [{}])[0].get('width', 256)

            # Get the base URL for tile requests
            base_url = info_url.replace('/info.json', '')

            # Find the highest resolution level
            # In IIIF, level 0 is typically the highest resolution
            # We'll use the full region at max quality
            max_level = 0

            # Check if we should use tiles or request the full image
            # For smaller images or if tiles aren't defined, request full image
            tiles_info = metadata.get('tiles', [])

            if not tiles_info or (width <= 2000 and height <= 2000):
                # Download as single full image
                logger.info(f"Downloading full image ({width}x{height})...")
                full_url = f"{base_url}/full/full/0/default.png"

                response = self.session.get(full_url, timeout=120)
                response.raise_for_status()

                from io import BytesIO
                img = Image.open(BytesIO(response.content))

                output_path = self.output_dir / output_filename
                img.save(output_path, 'PNG')
                logger.info(f"Saved: {output_path}")
                return True

            # For tiled images, download and composite tiles
            logger.info(f"Downloading tiled image ({width}x{height}, tile size: {tile_size})...")

            num_tiles_x, num_tiles_y = self.calculate_tile_grid(width, height, tile_size)
            total_tiles = num_tiles_x * num_tiles_y

            logger.info(f"Grid: {num_tiles_x}x{num_tiles_y} tiles ({total_tiles} total)")

            # Create the output image
            output_image = Image.new('RGB', (width, height), (255, 255, 255))

            # Download tiles
            tile_urls = []
            tile_positions = []

            for ty in range(num_tiles_y):
                for tx in range(num_tiles_x):
                    # Calculate region
                    x = tx * tile_size
                    y = ty * tile_size
                    w = min(tile_size, width - x)
                    h = min(tile_size, height - y)

                    # IIIF URL format: {base}/{region}/{size}/{rotation}/{quality}.{format}
                    region = f"{x},{y},{w},{h}"
                    tile_url = f"{base_url}/{region}/full/0/default.jpg"

                    tile_urls.append(tile_url)
                    tile_positions.append((x, y))

            # Download tiles with progress bar
            tiles_downloaded = 0
            tiles_failed = 0

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_pos = {
                    executor.submit(self.download_tile, url): (url, pos)
                    for url, pos in zip(tile_urls, tile_positions)
                }

                with tqdm(total=total_tiles, desc="Downloading tiles", unit="tile") as pbar:
                    for future in as_completed(future_to_pos):
                        url, pos = future_to_pos[future]
                        tile_img = future.result()

                        if tile_img:
                            output_image.paste(tile_img, pos)
                            tiles_downloaded += 1
                        else:
                            tiles_failed += 1

                        pbar.update(1)

            if tiles_failed > 0:
                logger.warning(f"{tiles_failed} tiles failed to download")

            # Save the composited image
            output_path = self.output_dir / output_filename
            output_image.save(output_path, 'PNG')
            logger.info(f"Saved: {output_path}")

            return True

        except Exception as e:
            logger.error(f"Failed to process {info_url}: {e}")
            return False

    def process_url(self, page_url: str):
        """
        Process a webpage URL and download all IIIF images.

        Args:
            page_url: URL of the webpage containing OpenSeadragon viewer
        """
        try:
            # Fetch the page
            html = self.fetch_page(page_url)

            # Extract IIIF sources
            tile_sources = self.extract_openseadragon_sources(html)

            if not tile_sources:
                logger.error("No IIIF manifests found in the page")
                return

            # Download each image
            for idx, info_url in enumerate(tile_sources, 1):
                logger.info(f"\nProcessing image {idx}/{len(tile_sources)}")

                # Generate output filename
                output_filename = f"image_{idx:03d}.png"

                # Download and composite
                success = self.download_and_composite_image(info_url, output_filename)

                if not success:
                    logger.warning(f"Skipping image {idx} due to errors")

            logger.info(f"\nCompleted! Images saved to: {self.output_dir}")

        except Exception as e:
            logger.error(f"Error processing URL: {e}")
            sys.exit(1)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Download full-resolution images from IIIF manifests in OpenSeadragon viewers'
    )
    parser.add_argument(
        'url',
        help='URL of the webpage containing OpenSeadragon viewer'
    )
    parser.add_argument(
        '--output', '-o',
        default='.',
        help='Output directory for downloaded images (default: current directory)'
    )
    parser.add_argument(
        '--workers', '-w',
        type=int,
        default=10,
        help='Maximum number of concurrent download threads (default: 10)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Create downloader and process URL
    downloader = IIIFImageDownloader(
        output_dir=args.output,
        max_workers=args.workers
    )

    downloader.process_url(args.url)


if __name__ == '__main__':
    main()
