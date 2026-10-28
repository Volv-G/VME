// Versions from kit/versions.toml, shared with every other app on the kit.
// They are not a preference: RootEncoder 2.7.5 forces compileSdk 36 (and so
// AGP 8.13) and Kotlin 2.3. The catalog spells out how, and the attempts
// with older versions that did not compile.
plugins {
    alias(kit.plugins.android.application) apply false
    alias(kit.plugins.kotlin.android) apply false
}
