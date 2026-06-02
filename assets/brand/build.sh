#!/usr/bin/env bash
# Regenerate the Vaillant Gateway HA brand images from icon.svg.
#
# Emits the four PNGs Home Assistant serves from the integration's brand/
# folder (local brands-proxy, HA 2026.3+):
#   icon.png   256x256       icon@2x.png 512x512
#   logo.png   (height 200)  logo@2x.png (height 400)   emblem + "Vaillant"
#
# The artwork is an ORIGINAL stylized hare-in-oval (see icon.svg) — not
# Vaillant's trademarked logo — and the wordmark is set in Nimbus Sans Bold,
# not Vaillant's typeface. Requires ImageMagick + the Nimbus/DejaVu fonts.
#
# Usage:  assets/brand/build.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/icon.svg"
out="$here/../../custom_components/vaillant_gateway/brand"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

teal="#00897B"
font="Nimbus-Sans-Bold"

mkdir -p "$out"

# hi-res emblem from the vector source
convert -background none -density 576 "$src" -resize 1024x1024 PNG32:"$work/icon_1024.png"
convert "$work/icon_1024.png" -trim +repage -resize x600 "$work/emblem.png"

# wordmark (label metadata cleared so it never renders as a caption)
convert -background none -fill "$teal" -font "$font" -pointsize 760 \
        label:'Vaillant' -trim +repage -resize x344 -set label '' "$work/word.png"

# compose emblem + gap + wordmark, vertically centred
ww=$(identify -format %w "$work/word.png")
convert "$work/word.png" -background none -gravity center -extent "${ww}x600" "$work/word_pad.png"
convert -size 110x600 xc:none "$work/gap.png"
convert "$work/emblem.png" "$work/gap.png" "$work/word_pad.png" +append \
        -background none -bordercolor none -border 52 PNG32:"$work/logo_master.png"

# emit the four HA brand files
convert "$work/icon_1024.png"   -resize 256x256 PNG32:"$out/icon.png"
convert "$work/icon_1024.png"   -resize 512x512 PNG32:"$out/icon@2x.png"
convert "$work/logo_master.png" -resize x200    PNG32:"$out/logo.png"
convert "$work/logo_master.png" -resize x400    PNG32:"$out/logo@2x.png"

echo "Wrote brand images to $out:"
identify -format '  %f  %wx%h\n' \
    "$out/icon.png" "$out/icon@2x.png" "$out/logo.png" "$out/logo@2x.png"
