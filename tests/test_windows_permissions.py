"""Exercise real Windows review controls with synthetic requests only."""
import sys
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows dialog")
class WindowsPermissionDialogTests(unittest.TestCase):
    def test_complete_details_buttons_and_safe_keyboard_defaults(self):
        import tkinter as tk
        from smith_agents.platform_win32 import permission_dialog
        root = tk.Tk()
        root.withdraw()
        request = {"tool_name": "Bash", "input": {"command": "echo test\n" * 100},
                   "decision_reason": "Synthetic UI test"}
        try:
            for action in ("Allow once", "Deny", "Cancel", "<Return>", "<Escape>"):
                dialog, result = permission_dialog({"name": "Test session", "cwd": "C:/test"}, {"request": request})
                dialog.update()
                widgets = []
                def walk(parent):
                    for child in parent.winfo_children():
                        widgets.append(child)
                        walk(child)
                walk(dialog)
                details = next(w for w in widgets if isinstance(w, tk.Text))
                self.assertIn('Synthetic UI test', details.get('1.0', 'end'))
                self.assertEqual(str(details.cget('state')), 'disabled')
                if action.startswith('<'):
                    dialog.focus_force()
                    dialog.update()
                    dialog.event_generate(action)
                else:
                    next(w for w in widgets if w.winfo_class() == 'TButton' and w.cget('text') == action).invoke()
                dialog.update()
                self.assertFalse(dialog.winfo_exists(), action)
                self.assertEqual(result['choice'], {'Allow once': 'allow', 'Deny': 'deny'}.get(action))
        finally:
            root.destroy()
