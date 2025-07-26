import discord
import aiohttp
import os
import random
import json
from datetime import datetime
from discord import app_commands
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
TMDB_API_KEY = os.getenv('TMDB_API_KEY')

if not DISCORD_TOKEN:
    raise ValueError("Missing DISCORD_TOKEN environment variable")

# Weather condition mapping (WMO codes)
WEATHER_CODES = {
    0: "☀️ Clear sky",
    1: "🌤️ Mainly clear",
    2: "⛅ Partly cloudy",
    3: "☁️ Overcast",
    45: "🌫️ Fog",
    48: "🌫️ Rime fog",
    51: "🌧️ Light drizzle",
    53: "🌧️ Moderate drizzle",
    55: "🌧️ Dense drizzle",
    56: "🌧️❄️ Light freezing drizzle",
    57: "🌧️❄️ Dense freezing drizzle",
    61: "🌧️ Slight rain",
    63: "🌧️ Moderate rain",
    65: "🌧️ Heavy rain",
    66: "🌧️❄️ Light freezing rain",
    67: "🌧️❄️ Heavy freezing rain",
    71: "❄️ Slight snow",
    73: "❄️ Moderate snow",
    75: "❄️ Heavy snow",
    77: "❄️ Snow grains",
    80: "🌦️ Slight rain showers",
    81: "🌦️ Moderate rain showers",
    82: "🌦️ Violent rain showers",
    85: "🌨️ Slight snow showers",
    86: "🌨️ Heavy snow showers",
    95: "⛈️ Thunderstorm",
    96: "⛈️💧 Thunderstorm with slight hail",
    99: "⛈️🧊 Thunderstorm with heavy hail"
}

class MyClient(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tree = app_commands.CommandTree(self)
        self.session = None
        self.nominatim_headers = {'User-Agent': 'DiscordWeatherBot/1.0 (non-profit educational project)'}
        self.user_locations = self.load_user_locations()
        
        # Updated genre mapping with reliable combinations
        self.genre_mapping = {
            "happy": [35, 10402],     # Comedy, Music
            "sad": [18, 10751],       # Drama, Family
            "excited": [28, 12],      # Action, Adventure
            "scared": [27],           # Horror
            "thoughtful": [9648, 18]  # Mystery, Drama
        }

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        await super().close()
        
    def load_user_locations(self):
        try:
            with open('user_locations.json', 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_user_locations(self):
        with open('user_locations.json', 'w') as f:
            json.dump(self.user_locations, f)

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        await self.tree.sync()
        print("Commands synced!")

    async def on_ready(self):
        print(f'Logged on as {self.user} (ID: {self.user.id})')
        await self.change_presence(activity=discord.Game(name="/help"))

    async def get_meme(self):
        try:
            async with self.session.get('https://meme-api.com/gimme') as response:
                data = await response.json()
                return data['url'], None
        except (aiohttp.ClientError, KeyError) as e:
            return None, f"🚫 Failed to get meme: {type(e).__name__}"

    async def get_quote(self):
        """Robust quote fetching with multiple fallback APIs"""
        quote_apis = [
            'https://api.quotable.io/random',
            'https://zenquotes.io/api/random',
            'https://api.forismatic.com/api/1.0/?method=getQuote&format=json&lang=en'
        ]
        
        for api_url in quote_apis:
            try:
                async with self.session.get(api_url, timeout=3) as response:
                    data = await response.json()
                    
                    # Handle different API response formats
                    if api_url == 'https://zenquotes.io/api/random':
                        quote = data[0]['q']
                        author = data[0]['a']
                    elif api_url == 'https://api.forismatic.com/api/1.0/':
                        quote = data['quoteText'].strip()
                        author = data['quoteAuthor'] or "Unknown"
                    else:  # Default quotable.io format
                        quote = data['content']
                        author = data['author']
                    
                    return f"\"{quote}\"\n- {author}", None
                    
            except (aiohttp.ClientError, KeyError, IndexError, json.JSONDecodeError):
                continue  # Try next API if this one fails
                
        return None, "🚫 All quote services are unavailable right now"

    async def geocode_location(self, location: str):
        """Convert location string to coordinates"""
        try:
            geocode_url = 'https://nominatim.openstreetmap.org/search'
            params = {'q': location, 'format': 'json', 'limit': 1}
            async with self.session.get(geocode_url, params=params, headers=self.nominatim_headers) as response:
                geo_data = await response.json()
                if not geo_data:
                    return None, "📍 Location not found"
                
                lat = geo_data[0]['lat']
                lon = geo_data[0]['lon']
                display_name = geo_data[0]['display_name']
                
                return (lat, lon, display_name), None
        except (aiohttp.ClientError, KeyError, IndexError) as e:
            return None, f"🚫 Geocoding error: {type(e).__name__}"

    async def get_weather_data(self, lat: str, lon: str):
        """Get weather data from Open-Meteo API"""
        try:
            weather_url = 'https://api.open-meteo.com/v1/forecast'
            weather_params = {
                'latitude': lat,
                'longitude': lon,
                'current': 'temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code',
                'daily': 'temperature_2m_max,temperature_2m_min,weather_code',
                'wind_speed_unit': 'ms',
                'timezone': 'auto',
                'forecast_days': 1
            }
            async with self.session.get(weather_url, params=weather_params) as response:
                weather_data = await response.json()
                
                if 'error' in weather_data:
                    return None, weather_data['reason']
                
                return weather_data, None
        except (aiohttp.ClientError, KeyError) as e:
            return None, f"🚫 Weather API error: {type(e).__name__}"

    async def get_weather(self, location: str, user_id: str = None):
        """Get weather for a location with enhanced formatting"""
        try:
            # Check if user has a saved location
            if user_id and user_id in self.user_locations:
                lat, lon, display_name = self.user_locations[user_id]
            else:
                # Geocode new location
                geocode_result, error = await self.geocode_location(location)
                if error:
                    return None, error
                lat, lon, display_name = geocode_result
                
                # Save new location for user
                if user_id:
                    self.user_locations[user_id] = (lat, lon, display_name)
                    self.save_user_locations()
            
            # Get weather data
            weather_data, error = await self.get_weather_data(lat, lon)
            if error:
                return None, error
            
            current = weather_data['current']
            daily = weather_data['daily']
            
            # Get emoji for weather code
            weather_emoji = WEATHER_CODES.get(current['weather_code'], "🌡️")
            
            # Format date
            today = datetime.now().strftime("%A, %B %d")
            
            # Create weather report
            return (
                f"## {weather_emoji} Weather in {display_name}\n"
                f"### {today}\n"
                f"**Conditions**: {WEATHER_CODES[current['weather_code']].split(' ', 1)[1]}\n"
                f"🌡️ **Temperature**: {current['temperature_2m']}°C\n"
                f"↕️ **Daily Range**: {daily['temperature_2m_min'][0]}°C - {daily['temperature_2m_max'][0]}°C\n"
                f"💧 **Humidity**: {current['relative_humidity_2m']}%\n"
                f"💨 **Wind**: {current['wind_speed_10m']} m/s\n\n"
                f"_Data from Open-Meteo • Location via OpenStreetMap_"
            ), None
                
        except (KeyError, IndexError, TypeError) as e:
            return None, f"🚫 Weather processing error: {type(e).__name__}"

    async def get_movie(self, mood: str = "random"):
        """Robust movie recommendation with fallback logic"""
        try:
            # Define base API parameters
            params = {
                'api_key': TMDB_API_KEY,
                'sort_by': 'popularity.desc',
                'include_adult': 'false',
                'vote_count.gte': 10,  # Very low threshold
                'page': random.randint(1, 10)  # Focused page range
            }
            
            # Get genre IDs for mood if not random
            if mood != "random":
                genre_ids = self.genre_mapping.get(mood, [])
                if genre_ids:
                    # Use OR logic instead of AND for genres
                    params['with_genres'] = "|".join(map(str, genre_ids))
            
            # Get movie list
            url = 'https://api.themoviedb.org/3/discover/movie'
            async with self.session.get(url, params=params) as response:
                data = await response.json()
                
                # If no results, try without genre filter
                if not data.get('results'):
                    if 'with_genres' in params:
                        params.pop('with_genres')
                        async with self.session.get(url, params=params) as fallback_response:
                            data = await fallback_response.json()
                    
                    if not data.get('results'):
                        return None, "🎬 No movies found. Try a different mood!"
                
                # Filter valid movies with posters
                valid_movies = [m for m in data['results'] if m.get('poster_path')]
                if not valid_movies:
                    return None, "🎬 No movies with posters found. Try again!"
                
                movie = random.choice(valid_movies)
                
                # Get movie details
                detail_url = f'https://api.themoviedb.org/3/movie/{movie["id"]}'
                async with self.session.get(detail_url, params={'api_key': TMDB_API_KEY}) as detail_response:
                    movie_data = await detail_response.json()
                
                # Format response
                poster = f"https://image.tmdb.org/t/p/w500{movie['poster_path']}"
                overview = movie['overview'][:200] + "..." if movie.get('overview') else "No description available"
                release_year = movie['release_date'][:4] if movie.get('release_date') else "Unknown year"
                genres = ", ".join([g['name'] for g in movie_data.get('genres', [])]) if movie_data.get('genres') else "Unknown"
                
                return (
                    f"🎬 **{movie['title']}** ({release_year})\n"
                    f"⭐ **Rating**: {movie['vote_average']}/10 • ⏱️ {movie_data.get('runtime', '?')} mins\n"
                    f"🎭 **Genres**: {genres}\n"
                    f"📝 **Plot**: {overview}\n"
                    f"{poster}"
                ), None
                
        except (aiohttp.ClientError, KeyError, IndexError) as e:
            return None, f"🚫 Failed to get movie: {type(e).__name__}"

# Command setup
client = MyClient(intents=discord.Intents.default())

@client.tree.command(name="meme", description="Get a random meme")
async def meme_command(interaction: discord.Interaction):
    await interaction.response.defer()
    meme, error = await client.get_meme()
    await (interaction.followup.send(meme) if meme else interaction.followup.send(error))

@client.tree.command(name="quote", description="Get an inspirational quote")
async def quote_command(interaction: discord.Interaction):
    await interaction.response.defer()
    quote, error = await client.get_quote()
    await (interaction.followup.send(quote) if quote else interaction.followup.send(error))

@client.tree.command(name="weather", description="Get weather for a location")
@app_commands.describe(
    location="City name or address (optional if you've set a location)",
    save="Save this as your default location?"
)
async def weather_command(interaction: discord.Interaction, location: str = None, save: bool = False):
    await interaction.response.defer()
    
    user_id = str(interaction.user.id)
    
    # If no location provided but user has saved location
    if not location and user_id in client.user_locations:
        _, _, display_name = client.user_locations[user_id]
        location = display_name
    
    if not location:
        return await interaction.followup.send("Please provide a location or set a default with `/setlocation`")
    
    weather, error = await client.get_weather(location, user_id if save else None)
    if save and not error:
        message = f"✅ Location saved! {weather}"
    else:
        message = weather if weather else error
    
    await interaction.followup.send(message)

@client.tree.command(name="setlocation", description="Set your default location for weather")
@app_commands.describe(location="Your city or address")
async def set_location_command(interaction: discord.Interaction, location: str):
    await interaction.response.defer()
    
    user_id = str(interaction.user.id)
    geocode_result, error = await client.geocode_location(location)
    
    if error:
        return await interaction.followup.send(error)
    
    lat, lon, display_name = geocode_result
    client.user_locations[user_id] = (lat, lon, display_name)
    client.save_user_locations()
    
    await interaction.followup.send(f"✅ Your default location has been set to: **{display_name}**")

@client.tree.command(name="mylocation", description="Show your saved weather location")
async def my_location_command(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    if user_id in client.user_locations:
        _, _, display_name = client.user_locations[user_id]
        await interaction.response.send_message(f"📍 Your saved location is: **{display_name}**")
    else:
        await interaction.response.send_message("You haven't set a default location yet. Use `/setlocation` to set one.")

@client.tree.command(name="movie", description="Get a movie recommendation")
@app_commands.describe(mood="Your current mood for movie suggestions")
@app_commands.choices(mood=[
    app_commands.Choice(name="😄 Happy", value="happy"),
    app_commands.Choice(name="😢 Sad", value="sad"),
    app_commands.Choice(name="🤩 Excited", value="excited"),
    app_commands.Choice(name="😨 Scared", value="scared"),
    app_commands.Choice(name="🤔 Thoughtful", value="thoughtful"),
    app_commands.Choice(name="🎲 Random", value="random")
])
async def movie_command(interaction: discord.Interaction, mood: str = "random"):
    await interaction.response.defer()
    movie, error = await client.get_movie(mood)
    await (interaction.followup.send(movie) if movie else interaction.followup.send(error))

@client.tree.command(name="help", description="Show available commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 Weather & Movie Bot Commands",
        description="Here are all the available commands:",
        color=0x3498db
    )
    
    commands = [
        ("/meme", "Get a random meme", False),
        ("/quote", "Get an inspirational quote", False),
        ("/weather [location]", "Get weather information (save with `save=True`)", False),
        ("/setlocation [location]", "Set your default weather location", False),
        ("/mylocation", "Show your saved weather location", False),
        ("/movie [mood]", "Get movie recommendation based on mood", False),
        ("/help", "Show this help message", False)
    ]
    
    for name, value, inline in commands:
        embed.add_field(name=name, value=value, inline=inline)
    
    embed.add_field(
        name="Movie Mood Options", 
        value="😄 Happy: Comedy/Musical\n"
              "😢 Sad: Drama/Family\n"
              "🤩 Excited: Action/Adventure\n"
              "😨 Scared: Horror\n"
              "🤔 Thoughtful: Mystery/Drama\n"
              "🎲 Random: Any popular movie",
        inline=False
    )
    
    embed.add_field(
        name="Weather Features", 
        value="• Save locations with `/setlocation`\n"
              "• Get weather with just `/weather` after setting location\n"
              "• Detailed weather reports with emojis",
        inline=False
    )
    
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

client.run(DISCORD_TOKEN)