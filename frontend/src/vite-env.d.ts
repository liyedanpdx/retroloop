/// <reference types="vite/client" />
// Vite's ambient types, which include the module declaration for `./index.css`.
// `npm run build` runs `tsc -b` first and cannot type a stylesheet import
// without them; #13 is the first change that made the build run at all.
