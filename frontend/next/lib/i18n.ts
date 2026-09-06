/* The dictionary moved into `lib/i18n/`, one module per owner, so that six page ports can
   run in parallel without all six editing one file. This shim is the whole reason that split
   touched no other file: module resolution prefers the FILE over the directory, so every
   existing `from '@/lib/i18n'` — `Header.tsx`, `useI18n.ts`, and the two shipped pages —
   still lands here and is forwarded, and the move stayed reviewable as a move.

   It is a pure re-export and must stay one. Anything declared here would shadow the barrel
   for those four importers and not for anyone writing `from '@/lib/i18n/index'`, which is two
   dictionaries wearing one name. Deleting the file is safe on its own terms — the barrel
   exports exactly these names — but it repoints those imports at the directory, so rewrite
   them in the same commit, then delete this. */

export * from './i18n/index';
