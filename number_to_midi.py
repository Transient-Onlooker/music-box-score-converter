
import mido
import argparse
import re

# --- Configuration ---
NUMBER_TO_MIDI_MAP = {
    '1': 41,  # F2
    '2': 43,  # G2
    '3': 48,  # C3
    '4': 50,  # D3
    '5': 52,  # E3
    '6': 53,  # F3
    '7': 55,  # G3
    '8': 57,  # A3
    '9': 58,  # A#3
    '10': 59, # B3
    '11': 60, # C4
    '12': 61, # C#4
    '13': 62, # D4
    '14': 63, # D#4
    '15': 64, # E4
    '16': 65, # F4
    '17': 66, # F#4
    '18': 67, # G4
    '19': 68, # G#4
    '20': 69, # A4
    '21': 70, # A#4
    '22': 71, # B4
    '23': 72, # C5
    '24': 73, # C#5
    '25': 74, # D5
    '26': 75, # D#5
    '27': 76, # E5
    '28': 77, # F5
    '29': 79, # G5
    '30': 81  # A5
}

TICKS_PER_8TH_NOTE = 240

def number_to_midi(num_str):
    num_str = num_str.strip()
    midi_note = NUMBER_TO_MIDI_MAP.get(num_str)
    if midi_note is None:
        print(f"Warning: Number '{num_str}' is not in the 1-30 range. Skipping.")
    return midi_note

def parse_number_string(number_string):
    """A more robust parser to correctly handle chords and durations."""
    # Normalize the input string by removing leading/trailing whitespace and ensuring it starts/ends with a delimiter
    s = number_string.strip()
    if not s.startswith('/'):
        s = '/' + s
    if not s.endswith('/'):
        s = s + '/'

    # Split the string into slots based on the delimiter
    slots = s.split('/')
    
    parsed_events = []
    # Start from index 1 to skip the empty string before the first '/'
    # End before the last element, which is an empty string after the last '/'
    i = 1
    while i < len(slots) - 1:
        slot_content = slots[i].strip()
        
        # This slot contains a note or chord
        if slot_content:
            notes = [number_to_midi(n) for n in slot_content.split()]
            notes = [n for n in notes if n is not None]

            # Look ahead to count subsequent empty slots for duration
            duration_in_slots = 1
            j = i + 1
            while j < len(slots) - 1 and not slots[j].strip():
                duration_in_slots += 1
                j += 1
            
            if notes:
                parsed_events.append({'notes': notes, 'duration': duration_in_slots})
            
            # Move the main index past the current note and all its duration slots
            i = j
        # This slot is empty and not part of a duration, so it's a leading/isolated rest (which we ignore for now)
        else:
            i += 1
            
    return parsed_events

def validate_events_pre_creation(parsed_events):
    MIDI_TO_NUMBER_MAP = {v: k for k, v in NUMBER_TO_MIDI_MAP.items()}
    for i in range(len(parsed_events) - 1):
        if parsed_events[i]['duration'] == 1 and parsed_events[i+1]['duration'] == 1:
            if not set(parsed_events[i]['notes']).isdisjoint(set(parsed_events[i+1]['notes'])):
                print("\n--- Pre-creation Validation Error ---")
                print(f"Rule violation: Consecutive 8th notes detected for one of the notes in {parsed_events[i]['notes']}.")
                return False
    return True

def validate_midi_post_creation(filename):
    try:
        mid = mido.MidiFile(filename)
        ticks_for_8th = mid.ticks_per_beat / 2

        for track in mid.tracks:
            notes_on = {}
            last_note_off_time = {}
            
            absolute_time = 0
            for msg in track:
                absolute_time += msg.time
                if msg.type == 'note_on' and msg.velocity > 0:
                    if msg.note in last_note_off_time:
                        time_since_last_off = absolute_time - last_note_off_time[msg.note]
                        if time_since_last_off < ticks_for_8th:
                             print("\n--- Post-creation Validation Error ---")
                             print(f"MIDI validation failed: Note {msg.note} re-triggered too quickly ({time_since_last_off} ticks).")
                             return False
                    notes_on[msg.note] = absolute_time

                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    if msg.note in notes_on:
                        duration = absolute_time - notes_on[msg.note]
                        if duration < ticks_for_8th:
                            last_note_off_time[msg.note] = absolute_time
                        del notes_on[msg.note]

    except Exception as e:
        print(f"An error occurred during post-creation validation: {e}")
        return False
        
    print("--- Post-creation Validation: OK ---")
    return True

def create_midi_file(parsed_events, output_filename, ticks_per_beat=480):
    mid = mido.MidiFile(type=1)
    track_treble = mido.MidiTrack()
    track_bass = mido.MidiTrack()
    mid.tracks.extend([track_treble, track_bass])

    track_treble.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(150), time=0))

    treble_events_raw = []
    bass_events_raw = []
    for event in parsed_events:
        treble_notes = [note for note in event['notes'] if note >= 60]
        bass_notes = [note for note in event['notes'] if note < 60]
        duration = event['duration']

        treble_events_raw.append({'type': 'note' if treble_notes else 'sustain', 'notes': treble_notes, 'duration': duration})
        bass_events_raw.append({'type': 'note' if bass_notes else 'sustain', 'notes': bass_notes, 'duration': duration})

    def merge_sustain_events(raw_events):
        merged = []
        for event in raw_events:
            if event['type'] == 'note':
                merged.append(event.copy())
            elif event['type'] == 'sustain':
                if merged and merged[-1]['type'] == 'note':
                    merged[-1]['duration'] += event['duration']
                else:
                    merged.append({'type': 'rest', 'duration': event['duration']})
        return merged

    final_treble_events = merge_sustain_events(treble_events_raw)
    final_bass_events = merge_sustain_events(bass_events_raw)

    def write_track_from_final_events(track, final_events):
        pending_delay = 0
        for event in final_events:
            duration_ticks = event['duration'] * TICKS_PER_8TH_NOTE
            if event['type'] == 'note':
                notes = event['notes']
                track.append(mido.Message('note_on', note=notes[0], velocity=80, time=pending_delay))
                for note in notes[1:]:
                    track.append(mido.Message('note_on', note=note, velocity=80, time=0))
                pending_delay = 0
                track.append(mido.Message('note_off', note=notes[0], velocity=80, time=duration_ticks))
                for note in notes[1:]:
                    track.append(mido.Message('note_off', note=note, velocity=80, time=0))
            elif event['type'] == 'rest':
                pending_delay += duration_ticks
    
    write_track_from_final_events(track_treble, final_treble_events)
    write_track_from_final_events(track_bass, final_bass_events)

    mid.save(output_filename)
    print(f"Successfully created two-track MIDI file: {output_filename}")

def main():
    parser = argparse.ArgumentParser(description="Converts a number-based music file to a MIDI file.")
    parser.add_argument("input_file", help="Path to the input number text file.")
    parser.add_argument("-o", "--output", dest="output_file", default="output.mid", help="Path for the output MIDI file.")
    args = parser.parse_args()

    try:
        with open(args.input_file, 'r', encoding='utf-8') as f:
            number_content = f.read()
        
        if not number_content.strip():
            print("Warning: The input file is empty.")
            return

        parsed_events = parse_number_string(number_content)
        
        if not validate_events_pre_creation(parsed_events):
            return

        create_midi_file(parsed_events, args.output_file)

        print("--- Calling post-creation validation ---")
        if not validate_midi_post_creation(args.output_file):
            print("The created MIDI file has issues. Please review the input.")

    except FileNotFoundError:
        print(f"Error: Input file not found at '{args.input_file}'")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
