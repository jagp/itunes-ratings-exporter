# itunes-ratings-exporter
Makes your itunes ratings/plays metadata portable

## Usage

### Libraries on a NAS or network share

Works with libraries hosted on a NAS (Synology, etc.), whether reached
through a mapped drive letter or a UNC path:

```
python -m itunes_ratings_exporter --library "Z:\Music\iTunes\iTunes Music Library.xml"
```

Exported `file_path` values preserve whichever form iTunes recorded —
`Z:\Music\song.mp3` for a mapped drive, `\SERVER\share\song.mp3` for a UNC
path. Mapped drive letters are per-machine, so a library recorded as `Z:`
resolves only where that mapping exists.
