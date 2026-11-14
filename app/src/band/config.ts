// The team's band number: the one written on the band's bag (7 means Vexo-HACK-07).
// Ask the team; never guess it and never use an example number. The app connects to this
// band only, and only after someone presses Connect. null = not set: the dashboard runs on
// canned data and the Connect buttons explain what to do.
export const BAND_NUMBER: number | null = null;

// Motion rate asked from the band (samples/second). 50 is plenty for the live chart; HAV
// measurement proper needs fast motion (firmware 1.1, up to 416 with both sensors on Bluetooth).
export const MOTION_RATE = 50;

// Seconds of motion kept for the chart and the RMS values.
export const WINDOW_SECONDS = 5;

// No motion sample for this long = stale data.
export const STALE_MS = 1500;
