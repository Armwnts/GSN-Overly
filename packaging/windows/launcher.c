/* GenshinOverlay.exe : lanceur Windows.
 * Au premier lancement, exécute app\setup.ps1 (installe Python + bibliothèques
 * dans le dossier runtime\), puis démarre l'overlay avec runtime\pythonw.exe. */
#include <windows.h>
#include <shlwapi.h>

static wchar_t root[MAX_PATH];

static void path(wchar_t *out, const wchar_t *rel) {
    lstrcpyW(out, root);
    PathAppendW(out, rel);
}

static int run_wait(wchar_t *cmd, const wchar_t *cwd, DWORD flags, DWORD *code) {
    STARTUPINFOW si = { sizeof(si) };
    PROCESS_INFORMATION pi;
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, flags, NULL, cwd, &si, &pi)) return 0;
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 1;
}

int WINAPI wWinMain(HINSTANCE h, HINSTANCE p, PWSTR args, int show) {
    wchar_t ready[MAX_PATH], pyw[MAX_PATH], app[MAX_PATH], setup[MAX_PATH], run[MAX_PATH];
    wchar_t cmd[4 * MAX_PATH];
    DWORD code = 1;

    GetModuleFileNameW(NULL, root, MAX_PATH);
    PathRemoveFileSpecW(root);
    path(ready, L"runtime\\.ready-v21");
    path(pyw, L"runtime\\pythonw.exe");
    path(app, L"app");
    path(setup, L"app\\setup.ps1");
    path(run, L"app\\run.py");

    if (GetFileAttributesW(ready) == INVALID_FILE_ATTRIBUTES) {
        wsprintfW(cmd, L"powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%s\"", setup);
        if (!run_wait(cmd, app, CREATE_NEW_CONSOLE, &code) || code != 0) {
            MessageBoxW(NULL,
                L"L'installation n'a pas pu se terminer.\n\n"
                L"Vérifie ta connexion Internet puis relance Genshin Overlay.\n"
                L"Détails : fichier app\\data\\setup.log",
                L"Genshin Overlay", MB_ICONERROR);
            return 1;
        }
    }

    STARTUPINFOW si = { sizeof(si) };
    PROCESS_INFORMATION pi;
    wsprintfW(cmd, L"\"%s\" \"%s\"", pyw, run);
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, 0, NULL, app, &si, &pi)) {
        MessageBoxW(NULL, L"Impossible de démarrer Python (dossier runtime\\ manquant ?).\n"
                          L"Supprime le dossier runtime puis relance pour réinstaller.",
                    L"Genshin Overlay", MB_ICONERROR);
        return 1;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
