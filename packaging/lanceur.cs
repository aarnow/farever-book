// "Farever France.exe": the launcher at the top of the shared folder.
//
// Starts the app with the Python that ships next to it (python\pythonw.exe,
// no console window), from wherever the folder was unzipped: a Windows
// shortcut (.lnk) keeps an absolute path, this does not. Built by
// packaging\make_zip.py with the C# compiler that comes with Windows.
//
// A zip downloaded from the Internet marks every file it holds as such, and
// .NET then refuses to load the window's assemblies (pythonnet): the app
// sat in the tray with no window. The marks are removed on the first run.
// And a start that dies at once is said, with its error, instead of nothing.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;

static class Lanceur
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool DeleteFile(string path);

    static void Unblock(string root)
    {
        string done = Path.Combine(root, @"app\.debloque");
        if (File.Exists(done)) return;
        foreach (string f in Directory.GetFiles(root, "*", SearchOption.AllDirectories))
            DeleteFile(f + ":Zone.Identifier");     // no mark: nothing happens
        try { File.WriteAllText(done, "ok"); } catch { }
    }

    static string Diagnose(string py, string app)
    {
        // the same start, with a console Python whose errors can be read
        var psi = new ProcessStartInfo(py,
            "-c \"import sys; sys.path.insert(0, 'meter'); "
            + "import frida, webview, PIL, clr; import app, menu_host; print('ok')\"");
        psi.WorkingDirectory = app;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        try
        {
            using (var p = Process.Start(psi))
            {
                string err = p.StandardError.ReadToEnd();
                string outp = p.StandardOutput.ReadToEnd();
                p.WaitForExit(60000);
                return (outp + err).Trim();
            }
        }
        catch (Exception e) { return e.Message; }
    }

    [STAThread]
    static void Main()
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string pyw = Path.Combine(root, @"python\pythonw.exe");
        string py = Path.Combine(root, @"python\python.exe");
        string app = Path.Combine(root, "app");
        string script = Path.Combine(app, @"meter\farever_meter.py");
        if (!File.Exists(pyw) || !File.Exists(script))
        {
            MessageBox.Show("Fichiers introuvables à côté du lanceur.\n\n"
                + "Décompresse tout le dossier « Farever France » avant de lancer, "
                + "sans déplacer le lanceur hors de ce dossier.",
                "Farever France", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        try { Unblock(root); } catch { }
        var psi = new ProcessStartInfo(pyw, "\"" + script + "\"");
        psi.WorkingDirectory = app;
        psi.UseShellExecute = false;
        // only the Python that ships here: no setting of another one leaks in
        psi.EnvironmentVariables.Remove("PYTHONHOME");
        psi.EnvironmentVariables.Remove("PYTHONPATH");
        Process proc;
        try
        {
            proc = Process.Start(psi);
        }
        catch (Exception e)
        {
            MessageBox.Show("Le démarrage a échoué :\n\n" + e.Message,
                "Farever France", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        // still running after 15 s: started (the app says the rest itself)
        if (proc.WaitForExit(15000) && proc.ExitCode != 0)
        {
            string detail = Diagnose(py, app);
            string report = Path.Combine(root, "erreur-demarrage.txt");
            try { File.WriteAllText(report, detail); } catch { }
            if (detail.Length > 1500) detail = "…" + detail.Substring(detail.Length - 1500);
            MessageBox.Show("Farever France s'est arrêté au démarrage (code "
                + proc.ExitCode + ").\n\n" + detail
                + "\n\nCe message est aussi dans « erreur-demarrage.txt », à côté du lanceur.",
                "Farever France", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
