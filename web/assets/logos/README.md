# Committee logos / profile pictures

Drop a committee's logo image here (e.g. `marketing.png`, `consult.jpg`) and set
that filename on the committee's Firestore document:

```
committees/{email}.logo = "marketing.png"
```

Notes:
- The value may also be a full `https://…` URL (used as-is).
- Recommended: square images, ~128×128px or larger; PNG/JPG/SVG/WebP.
- Shown as the committee's profile pic in the top bar when set; if blank, no
  image is shown (no broken icon).
