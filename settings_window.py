try:
    from tkinter import *  # Python 3.x
except ImportError:
    from Tkinter import *  # Python 2.x

class Window(Frame):

    def __init__(self, master, rbflow):
        Frame.__init__(self, master)
        self.rbflow = rbflow
        self.create_widgets(self.rbflow.get_login_info())

    def create_widgets(self, login_info):
        self.master.bind("<Return>", lambda x: self.save())
        Label(self.master, text="User: ").grid(row=0)
        self.user_input = Entry(self.master, width=15)
        self.user_input.grid(row=0, column=1)
        self.user_input.insert(END, login_info["user"])
        self.user_input.focus()

        Label(self.master, text="Password: ").grid(row=1)
        self.password_input = Entry(self.master, show='*', width=15)
        self.password_input.grid(row=1, column=1)
        self.password_input.insert(END, login_info["password"])

        Label(self.master, text="URL: ").grid(row=2)
        self.url_input = Entry(self.master, width=30)
        self.url_input.grid(row=2, column=1)
        self.url_input.insert(END, login_info["url"])

        self.save_button = Button(self.master, text="Save", fg="white", command=self.save).grid(row=3)

    def save(self):
        login_info = {
            "user": self.user_input.get(),
            "password": self.password_input.get(),
            "url": self.url_input.get()
        }
        self.rbflow.store_config(login_info)
        self.quit()


def open_settings(rbflow):
    root = Tk()
    app = Window(root, rbflow)
    root.wm_attributes('-topmost', 1)
    root.after(1, lambda: root.focus_force())
    root.mainloop()
