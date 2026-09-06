# lib unit tests

```
cd frontend/next
npm test              # node --experimental-strip-types --test "lib/__tests__/*.test.*"
npm run test:types    # tsc --noEmit -p lib/__tests__
```

Both live in `package.json` rather than only here, because an incantation that exists only in a
README is one nobody runs. `npm test` is the command; the flag is not optional — without
`--experimental-strip-types` Node reports every file as `ERR_UNKNOWN_FILE_EXTENSION`, which
reads like a broken suite rather than a missing flag.

**Why `.mts` and not `.test.ts`.** The project has no test transpiler, so Node runs the
TypeScript directly through its own type stripping — and Node's ESM resolver needs the real
extension on the import (`../format.ts`), while `tsc` rejects that specifier unless
`allowImportingTsExtensions` is on. Naming the tests `.mts` resolves the standoff: Node treats
them as ESM TypeScript, and the root `tsconfig.json`'s `**/*.ts` include pattern does not match
them, so `npx tsc --noEmit` at the app root stays clean.

That last part is also how these files used to escape typechecking altogether. `tsconfig.json`
in this directory is a second program covering them, with `allowImportingTsExtensions` switched
on for the tests alone — run by `npm run test:types`. The modules under test are typechecked
either way, by both programs; the tests themselves are only covered by the second.
