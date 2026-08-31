import argparse
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path


RINEX_FILE_PATTERN = re.compile(r"^.+\.[0-9]{2}[NO]$", re.IGNORECASE)
END_OF_HEADER = "END OF HEADER"
EPOCH_LINE = re.compile(r"^\s*(\d{2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})\s+([0-9.]+)\s+(\d+)")


def split_header(path):
    lines = path.read_text(encoding="ascii", errors="replace").splitlines(keepends=True)
    for index, line in enumerate(lines):
        if END_OF_HEADER in line:
            return lines[:index + 1], lines[index + 1:]
    raise ValueError(f"{path} has no END OF HEADER record")


def header_value(header, label):
    for line in header:
        if label in line:
            return line[:60].strip()
    return ""


def observation_types(header):
    value = header_value(header, "# / TYPES OF OBSERV")
    if not value:
        raise ValueError("observation header has no # / TYPES OF OBSERV record")
    return int(value.split()[0])


def epoch_timestamp(line):
    match = EPOCH_LINE.match(line)
    if not match:
        return None
    year = int(match.group(1))
    year += 2000 if year < 80 else 1900
    return datetime(year, *(int(match.group(index)) for index in range(2, 6)), int(float(match.group(6))))


def observation_blocks(data, observation_count):
    """Read complete RINEX 2 observation epochs, including continuation lines."""
    blocks = []
    index = 0
    lines_per_satellite = (observation_count + 4) // 5
    while index < len(data):
        line = data[index]
        timestamp = epoch_timestamp(line)
        if timestamp is None:
            index += 1
            continue

        satellite_count = int(line[29:32])
        satellite_list_lines = (satellite_count + 11) // 12
        block_length = satellite_list_lines + satellite_count * lines_per_satellite
        block = data[index:index + block_length]
        if len(block) != block_length:
            raise ValueError("incomplete observation epoch at end of file")
        blocks.append((timestamp, "".join(block)))
        index += block_length
    return blocks


def merge_navigation(files, output):
    header, _ = split_header(files[0])
    records = []
    for path in files:
        _, data = split_header(path)
        records.extend(data)
    output.write_text("".join(header + records), encoding="ascii")


def merge_observations(files, output):
    header, _ = split_header(files[0])
    count = observation_types(header)
    blocks = {}
    for path in files:
        source_header, data = split_header(path)
        if observation_types(source_header) != count:
            raise ValueError(f"observation type count differs in {path}")
        for timestamp, block in observation_blocks(data, count):
            blocks.setdefault((timestamp, block), block)

    ordered_blocks = [block for (_, block) in sorted(blocks.items())]
    output.write_text("".join(header + ordered_blocks), encoding="ascii")


def find_input_groups(root):
    groups = defaultdict(lambda: defaultdict(list))
    for path in root.iterdir():
        if not path.is_file() or not RINEX_FILE_PATTERN.match(path.name):
            continue
        groups[path.suffix[-1].upper()][path.parent].append(path)
    return groups


def merge_folder(folder, output_stem):
    groups = find_input_groups(folder)
    outputs = []
    for file_type, folders in groups.items():
        for source_folder, files in folders.items():
            files = [path for path in files if path.stem != output_stem]
            if len(files) < 2:
                continue
            files.sort(key=lambda path: path.name.lower())
            output = source_folder / f"{output_stem}{files[0].suffix}"
            if file_type == "N":
                merge_navigation(files, output)
            else:
                merge_observations(files, output)
            outputs.append((file_type, output, files))
    return outputs


def main():
    parser = argparse.ArgumentParser(description="Merge RINEX 2 navigation and observation files.")
    parser.add_argument("folder", type=Path, help="Folder containing the RINEX files")
    parser.add_argument("--output-stem", default="merged", help="Output filename stem")
    arguments = parser.parse_args()

    outputs = merge_folder(arguments.folder, arguments.output_stem)
    if not outputs:
        print("No folder contained at least two matching RINEX files.")
        return
    for file_type, output, files in outputs:
        print(f"Merged {len(files)} .{file_type} files -> {output.name}")


if __name__ == "__main__":
    main()
