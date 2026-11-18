# Los Angeles weather wallpaper — design example

This is a **configuration proposal**, not a working connector or generated wallpaper. There is no app/config loader for these names yet. It does not change the existing strict `jobs/` schema and must not be passed to `python3 -m pipeline` as a job.

The user's example is: “Generate a wallpaper whose content follows current Los Angeles weather.” The proposed first implementation selects one pre-rendered TD video/cover from normalized weather and day/night state. Continuous wind/cloud/rain effects are a later live-renderer option.

## Configuration

[.env.example](.env.example) contains empty model credentials and nonsecret weather settings. A future developer loader may read a deliberately selected local `.env`; the Mac app uses connection settings and Keychain. The agent key and weather-service key are independent. Free personal-development Open-Meteo requests need no weather API key; a commercial/custom provider must use its corresponding endpoint/auth adapter. Do not insert a model key into weather requests.

The planned request is `GET https://api.open-meteo.com/v1/forecast` with URL-encoded query parameters:

```text
latitude=34.0522
longitude=-118.2437
current=temperature_2m,is_day,precipitation,weather_code,cloud_cover,wind_speed_10m
temperature_unit=celsius
wind_speed_unit=ms
precipitation_unit=mm
timezone=America/Los_Angeles
```

Read the response's units, current-data timestamp/interval and UTC offset; do not infer freshness from request time. The provider's current conditions are model-derived. The proposed 900-second refresh is a project starting cadence, not a guaranteed provider update interval. See [API documentation](https://open-meteo.com/en/docs) and [service terms](https://open-meteo.com/en/terms).

## Expected behavior to implement

| Normalized state | Visual response |
| --- | --- |
| Clear / cloudy | Select the matching daytime/nighttime clip and cover |
| Rain / snow / storm / fog | Select the applicable authored variant; unsupported categories use a declared neutral fallback |
| Changed temperature/wind/cloud values | Optional text/properties, or continuous parameters in a future live renderer |
| Temporarily unavailable data | Keep a time-bounded last-good state and mark it stale in the app |
| Data older than the allowed grace period | Show the offline default; never fabricate a current weather observation |

The agent creates the art and binding once; ordinary refresh must not call the model or reopen TD. Cache and normalize data in the native data broker, share requests across screens, avoid retries every frame, and keep only the selected video actively decoding. Actual credentials never enter exported packages, model context or scene parameters.

Before calling this example complete, implement and test the mapping against recorded cases including errors/day-night/unit handling, query the real provider, generate the real TD assets, and verify app playback with TD closed. No current-weather query or asset generation was performed when writing this example. The parent [platform plan](../../plan/agent-platform.md) defines the implementation and resource gates.
