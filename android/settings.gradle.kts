pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "PowerPosAndroidHardware"

include(
    ":sunmi-sdk-adapter",
    ":printer-manager",
    ":cashdrawer-manager",
    ":hardware-monitor",
    ":offline-storage",
    ":sync-client",
    ":demo-app"
)
