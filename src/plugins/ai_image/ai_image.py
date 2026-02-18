from plugins.base_plugin.base_plugin import BasePlugin
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import base64
import random
import html
import feedparser
from utils.http_client import get_http_session
import logging

logger = logging.getLogger(__name__)

# Preset RSS news feeds
NEWS_FEEDS = {
    "bbc": ("BBC World News", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    "reuters": ("Reuters Top News", "https://www.rss-bridge.org/bridge01/?action=display&bridge=Reuters&feed=home%2Ftopnews&format=Atom"),
    "ap": ("AP Top News", "https://rsshub.app/apnews/topics/apf-topnews"),
    "npr": ("NPR News", "https://feeds.npr.org/1001/rss.xml"),
    "nyt": ("NY Times Headlines", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
    "tech": ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    "verge": ("The Verge", "https://www.theverge.com/rss/index.xml"),
}

# OpenAI models
OPENAI_IMAGE_MODELS = ["dall-e-3", "dall-e-2", "gpt-image-1"]
DEFAULT_OPENAI_MODEL = "dall-e-3"

# Gemini models
GEMINI_IMAGE_MODELS = ["imagen-4.0-generate-001", "imagen-4.0-fast-generate-001", "imagen-4.0-ultra-generate-001"]
DEFAULT_GEMINI_MODEL = "imagen-4.0-generate-001"


class AIImage(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        # Don't require a specific key - user chooses provider
        template_params['api_key'] = {
            "required": False,
            "service": "OpenAI or Google Gemini",
            "expected_key": "OPEN_AI_SECRET or GOOGLE_GEMINI_SECRET"
        }
        return template_params

    def generate_image(self, settings, device_config):
        logger.info("=== AI Image Plugin: Starting image generation ===")

        provider = settings.get("provider", "openai")
        text_prompt = settings.get("textPrompt", "")
        randomize_prompt = settings.get('randomizePrompt') == 'true'
        prompt_source = settings.get("promptSource", "manual")
        orientation = device_config.get_config("orientation")

        logger.info(f"Provider: {provider}, orientation: {orientation}, promptSource: {prompt_source}")

        # If news headlines mode, fetch a headline and use it as the prompt
        original_headline = None
        if prompt_source == "news":
            feed_urls = self._get_selected_feed_urls(settings)
            original_headline = self._fetch_news_headline(feed_urls)
            text_prompt = original_headline
            randomize_prompt = True  # Always randomize for news headlines
            logger.info(f"News headline selected: '{original_headline}'")

        logger.debug(f"Original prompt: '{text_prompt}'")
        logger.debug(f"Randomize prompt: {randomize_prompt}")

        image = None
        final_prompt = text_prompt  # Track the actual prompt used

        is_news = prompt_source == "news"
        if provider == "gemini":
            image, final_prompt = self._generate_with_gemini(settings, device_config, text_prompt, randomize_prompt, orientation, is_news)
        else:
            image, final_prompt = self._generate_with_openai(settings, device_config, text_prompt, randomize_prompt, orientation, is_news)

        if image:
            logger.info(f"AI image generated successfully: {image.size[0]}x{image.size[1]}")

            # Resize to display dimensions with letterboxing
            dimensions = device_config.get_resolution()
            if orientation == "vertical":
                dimensions = dimensions[::-1]

            # Get fit mode setting (default to 'fit' for letterbox)
            fit_mode = settings.get("fitMode", "fit")
            logger.debug(f"Resizing to {dimensions} with fit_mode={fit_mode}")

            image = self.image_loader.resize_image(image, dimensions, fit_mode=fit_mode)

            # Add title overlay — use original headline for news, final prompt otherwise
            show_title = settings.get("showTitle", "true") != "false"
            title = original_headline if original_headline else final_prompt
            if show_title and title:
                title = title.strip()
                image = self._add_title_overlay(image, title)
                logger.info(f"Added title overlay: {title}")

        logger.info("=== AI Image Plugin: Image generation complete ===")
        return image

    def _add_title_overlay(self, image: Image.Image, title: str) -> Image.Image:
        """Add title text overlay at the bottom of the image using full width."""
        img_with_overlay = image.copy()
        draw = ImageDraw.Draw(img_with_overlay, 'RGBA')

        width, height = img_with_overlay.size
        padding = 10
        max_text_width = width - (padding * 2)

        # Start with a reasonable font size and scale down if needed
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        target_font_size = max(14, int(height * 0.03))

        try:
            font = ImageFont.truetype(font_path, target_font_size)
        except Exception:
            font = ImageFont.load_default()
            target_font_size = 12

        # Check if text fits, reduce font size if needed
        bbox = draw.textbbox((0, 0), title, font=font)
        text_width = bbox[2] - bbox[0]

        while text_width > max_text_width and target_font_size > 10:
            target_font_size -= 1
            try:
                font = ImageFont.truetype(font_path, target_font_size)
            except Exception:
                break
            bbox = draw.textbbox((0, 0), title, font=font)
            text_width = bbox[2] - bbox[0]

        # If still too long, truncate with ellipsis
        display_title = title
        if text_width > max_text_width:
            while text_width > max_text_width and len(display_title) > 10:
                display_title = display_title[:-4] + "..."
                bbox = draw.textbbox((0, 0), display_title, font=font)
                text_width = bbox[2] - bbox[0]

        # Get final text dimensions
        bbox = draw.textbbox((0, 0), display_title, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Draw full-width semi-transparent background bar
        bar_height = text_height + (padding * 2)
        bar_top = height - bar_height
        draw.rectangle([0, bar_top, width, height], fill=(0, 0, 0, 180))

        # Center text in the bar
        x = (width - text_width) // 2
        y = bar_top + padding

        # Draw text
        draw.text((x, y), display_title, font=font, fill=(255, 255, 255, 255))

        return img_with_overlay

    def _get_selected_feed_urls(self, settings):
        """Build list of feed URLs from selected presets and custom URL."""
        feed_urls = []
        selected_feeds = settings.get("newsFeeds", "")
        if selected_feeds:
            for key in selected_feeds.split(","):
                key = key.strip()
                if key in NEWS_FEEDS:
                    feed_urls.append(NEWS_FEEDS[key][1])
        custom_url = settings.get("customFeedUrl", "").strip()
        if custom_url:
            feed_urls.append(custom_url)
        if not feed_urls:
            # Default to BBC if nothing selected
            feed_urls.append(NEWS_FEEDS["bbc"][1])
            logger.warning("No news feeds selected, defaulting to BBC")
        return feed_urls

    def _fetch_news_headline(self, feed_urls):
        """Fetch headlines from RSS feeds and return a random one."""
        session = get_http_session()
        all_headlines = []

        for url in feed_urls:
            try:
                resp = session.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
                for entry in feed.entries:
                    title = entry.get("title", "").strip()
                    if title:
                        all_headlines.append(html.unescape(title))
            except Exception as e:
                logger.warning(f"Failed to fetch RSS feed {url}: {e}")
                continue

        if not all_headlines:
            raise RuntimeError("Could not fetch any news headlines. Check feed URLs and network connectivity.")

        headline = random.choice(all_headlines)
        logger.info(f"Selected headline from {len(all_headlines)} total: '{headline}'")
        return headline

    def _generate_with_openai(self, settings, device_config, text_prompt, randomize_prompt, orientation, is_news=False):
        """Generate image using OpenAI DALL-E."""
        api_key = device_config.load_env_key("OPEN_AI_SECRET")
        if not api_key:
            logger.error("OpenAI API Key not configured")
            raise RuntimeError("OpenAI API Key not configured. Add OPEN_AI_SECRET in Settings > API Keys.")

        # Sanitize API key to ASCII (fixes copy/paste issues with special characters)
        api_key = api_key.encode('ascii', errors='ignore').decode('ascii').strip()

        image_model = settings.get('imageModel', DEFAULT_OPENAI_MODEL)
        if image_model not in OPENAI_IMAGE_MODELS:
            logger.error(f"Invalid OpenAI image model: {image_model}")
            raise RuntimeError("Invalid Image Model provided.")

        image_quality = settings.get('quality', "medium" if image_model == "gpt-image-1" else "standard")

        logger.info(f"OpenAI Settings: model={image_model}, quality={image_quality}")

        try:
            ai_client = OpenAI(api_key=api_key)

            if randomize_prompt:
                logger.debug("Generating randomized prompt using GPT-4...")
                text_prompt = self._fetch_openai_prompt(ai_client, text_prompt, is_news)
                text_prompt = text_prompt.encode('ascii', errors='ignore').decode('ascii')
                logger.info(f"Randomized prompt: '{text_prompt}'")

            logger.info(f"Generating image with {image_model}...")
            image = self._fetch_openai_image(ai_client, text_prompt, image_model, image_quality, orientation)
            return image, text_prompt

        except Exception as e:
            logger.error(f"Failed to make OpenAI request: {str(e)}")
            raise RuntimeError("OpenAI request failure, please check logs.")

    def _generate_with_gemini(self, settings, device_config, text_prompt, randomize_prompt, orientation, is_news=False):
        """Generate image using Google Gemini Imagen."""
        api_key = device_config.load_env_key("GOOGLE_GEMINI_SECRET")
        if not api_key:
            logger.error("Google Gemini API Key not configured")
            raise RuntimeError("Google Gemini API Key not configured. Add GOOGLE_GEMINI_SECRET in Settings > API Keys.")

        # Sanitize API key
        api_key = api_key.encode('ascii', errors='ignore').decode('ascii').strip()

        image_model = settings.get('geminiImageModel', DEFAULT_GEMINI_MODEL)

        logger.info(f"Gemini Settings: model={image_model}")

        try:
            from google import genai

            client = genai.Client(api_key=api_key)

            if randomize_prompt:
                logger.debug("Generating randomized prompt using Gemini...")
                text_prompt = self._fetch_gemini_prompt(client, text_prompt, is_news)
                logger.info(f"Randomized prompt: '{text_prompt}'")

            enhanced_prompt = text_prompt + (
                ". The image should fully occupy the entire canvas without any frames, "
                "borders, or cropped areas. No blank spaces or artificial framing."
            )

            logger.info(f"Generating image with Gemini {image_model}...")

            # Determine aspect ratio based on orientation
            if orientation == "horizontal":
                aspect_ratio = "16:9"
            else:
                aspect_ratio = "9:16"

            result = client.models.generate_images(
                model=image_model,
                prompt=enhanced_prompt,
                config={
                    "number_of_images": 1,
                    "aspect_ratio": aspect_ratio,
                }
            )

            if result.generated_images:
                # Get the first image
                img_data = result.generated_images[0].image
                # Convert to PIL Image
                img = Image.open(BytesIO(img_data.image_bytes))
                return img, text_prompt
            else:
                raise RuntimeError("Gemini returned no images")

        except ImportError:
            logger.error("google-genai package not installed")
            raise RuntimeError("Google Gemini SDK not installed. Run: pip install google-genai")
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to make Gemini request: {error_msg}")

            # Provide user-friendly error messages
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                raise RuntimeError("Gemini rate limit reached. Please wait a minute and try again, or try a different model.")
            elif "API_KEY" in error_msg.upper() or "401" in error_msg:
                raise RuntimeError("Gemini API key is invalid. Please check your GOOGLE_GEMINI_SECRET in Settings > API Keys.")
            elif "404" in error_msg:
                raise RuntimeError("Gemini model not found. Please select a different model.")
            else:
                raise RuntimeError(f"Gemini error: {error_msg[:100]}")

    def _fetch_openai_image(self, ai_client, prompt, model, quality, orientation):
        """Fetch image from OpenAI API."""
        prompt = prompt.encode('ascii', errors='ignore').decode('ascii')

        logger.info(f"Generating image for prompt: {prompt}, model: {model}, quality: {quality}")
        prompt += (
            ". The image should fully occupy the entire canvas without any frames, "
            "borders, or cropped areas. No blank spaces or artificial framing."
        )

        args = {
            "model": model,
            "prompt": prompt,
            "size": "1024x1024",
        }
        if model == "dall-e-3":
            args["size"] = "1792x1024" if orientation == "horizontal" else "1024x1792"
            args["quality"] = quality
        elif model == "gpt-image-1":
            args["size"] = "1536x1024" if orientation == "horizontal" else "1024x1536"
            args["quality"] = quality

        response = ai_client.images.generate(**args)
        if model in ["dall-e-3", "dall-e-2"]:
            image_url = response.data[0].url
            session = get_http_session()
            response = session.get(image_url)
            img = Image.open(BytesIO(response.content))
        elif model == "gpt-image-1":
            image_base64 = response.data[0].b64_json
            image_bytes = base64.b64decode(image_base64)
            img = Image.open(BytesIO(image_bytes))
        return img

    def _fetch_openai_prompt(self, ai_client, from_prompt=None, is_news=False):
        """Generate a creative prompt using OpenAI."""
        logger.info("Getting random image prompt from OpenAI...")

        system_content = (
            "You are a creative assistant generating extremely random and unique image prompts. "
            "Avoid common themes. Focus on unexpected, unconventional, and bizarre combinations "
            "of art style, medium, subjects, time periods, and moods. No repetition. Prompts "
            "should be 20 words or less and specify random artist, movie, tv show or time period "
            "for the theme. Do not provide any headers or repeat the request, just provide the "
            "updated prompt in your response."
        )
        user_content = (
            "Give me a completely random image prompt, something unexpected and creative! "
            "Let's see what your AI mind can cook up!"
        )
        if from_prompt and from_prompt.strip() and is_news:
            system_content = (
                "You are a creative editorial illustrator. Given a news headline, generate "
                "a vivid image prompt that illustrates the story. Think bold editorial "
                "illustration style — dramatic, evocative, symbolic imagery. Keep it 20 words "
                "or less. Do not include any text or words in the image. Just provide the "
                "prompt, no explanation."
            )
            user_content = (
                f"News headline: \"{from_prompt}\"\n"
                "Create a vivid editorial illustration prompt for this headline."
            )
        elif from_prompt and from_prompt.strip():
            system_content = (
                "You are a creative assistant specializing in generating highly descriptive "
                "and unique prompts for creating images. When given a short or simple image "
                "description, your job is to rewrite it into a more detailed, imaginative, "
                "and descriptive version that captures the essence of the original while "
                "making it unique and vivid. Avoid adding irrelevant details but feel free "
                "to include creative and visual enhancements. Avoid common themes. Focus on "
                "unexpected, unconventional, and bizarre combinations of art style, medium, "
                "subjects, time periods, and moods. Do not provide any headers or repeat the "
                "request, just provide your updated prompt in the response. Prompts "
                "should be 20 words or less and specify random artist, movie, tv show or time "
                "period for the theme."
            )
            user_content = (
                f"Original prompt: \"{from_prompt}\"\n"
                "Rewrite it to make it more detailed, imaginative, and unique while staying "
                "true to the original idea. Include vivid imagery and descriptive details. "
                "Avoid changing the subject of the prompt."
            )

        response = ai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content}
            ],
            temperature=1
        )

        prompt = response.choices[0].message.content.strip()
        logger.info(f"Generated random image prompt: {prompt}")
        return prompt

    def _fetch_gemini_prompt(self, client, from_prompt=None, is_news=False):
        """Generate a creative prompt using Gemini."""
        logger.info("Getting random image prompt from Gemini...")

        if from_prompt and from_prompt.strip() and is_news:
            prompt_request = (
                f"News headline: \"{from_prompt}\"\n"
                "Create a vivid editorial illustration prompt for this headline. "
                "Think bold, dramatic, evocative, symbolic imagery in editorial illustration style. "
                "Do not include any text or words in the image. Keep it 20 words or less. "
                "Just provide the prompt, no explanation."
            )
        elif from_prompt and from_prompt.strip():
            prompt_request = (
                f"Take this image description: \"{from_prompt}\"\n"
                "Rewrite it to make it more detailed, imaginative, and unique while staying "
                "true to the original idea. Include vivid imagery and descriptive details. "
                "Focus on unexpected, unconventional, and bizarre combinations of art style, "
                "medium, subjects, time periods, and moods. Keep it 20 words or less. "
                "Just provide the prompt, no explanation."
            )
        else:
            prompt_request = (
                "Generate a completely random and unique image prompt. Focus on unexpected, "
                "unconventional, and bizarre combinations of art style, medium, subjects, "
                "time periods, and moods. Include a random artist, movie, tv show or time period. "
                "Keep it 20 words or less. Just provide the prompt, no explanation."
            )

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt_request
        )
        prompt = response.text.strip()
        logger.info(f"Generated random image prompt: {prompt}")
        return prompt
