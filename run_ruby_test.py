import os
import re
import thread
import subprocess
import functools
import time
import sublime
import sublime_plugin

test_pannels = {}

class AsyncProcess(object):
  def __init__(self, cmd, listener):
    self.cmd = cmd
    self.listener = listener
    print "DEBUG_EXEC: " + self.cmd
    self.proc = subprocess.Popen([self.cmd], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if self.proc.stdout:
      thread.start_new_thread(self.read_stdout, ())
    if self.proc.stderr:
      thread.start_new_thread(self.read_stderr, ())

  def read_stdout(self):
    while True:
      data = os.read(self.proc.stdout.fileno(), 2**15)

      if data != "":
        # TODO: Could refactor this to utilize metaprogramming but it might make it less readable?
        regex_pend = r'(^\s{4}(Given|When|Then|And|But).+?$\n\s{6}TODO.+?$)'
        regex_error = r'(^\s{4}(Given|When|Then|And|But).+?$\n\s{6}(expected.+|.*Error.*|.*error.*|.*NotFound.*|.*failed.*|.*Invalid.*)?$)'

        pend_line_match = re.search(re.compile(regex_pend, re.M), data)
        error_line_match = re.search(re.compile(regex_error, re.M), data)

        if pend_line_match is not None:
          line_text = pend_line_match.group(0).split("\n      ")

          data = re.compile(regex_pend, re.M).sub(line_text[0] + " #PEND" + "\n      " + line_text[1], data)

        if error_line_match is not None:
          line_text = error_line_match.group(0).split("\n      ")

          data = re.compile(regex_error, re.M).sub(line_text[0] + " #ERROR" + "\n      " + line_text[1], data)

        sublime.set_timeout(functools.partial(self.listener.append_data, self.proc, data), 0)
      else:
        self.proc.stdout.close()
        self.listener.is_running = False
        break

  def read_stderr(self):
    while True:
      data = os.read(self.proc.stderr.fileno(), 2**15)
      if data != "":
        sublime.set_timeout(functools.partial(self.listener.append_data, self.proc, data), 0)
      else:
        self.proc.stderr.close()
        self.listener.is_running = False
        break

class StatusProcess(object):
  def __init__(self, msg, listener):
    self.msg = msg
    self.listener = listener
    thread.start_new_thread(self.run_thread, ())

  def run_thread(self):
    progress = ""
    while True:
      if self.listener.is_running:
        if len(progress) >= 10:
          progress = ""
        progress += "."
        sublime.set_timeout(functools.partial(self.listener.update_status, self.msg, progress), 0)
        time.sleep(1)
      else:
        break

def wrap_in_cd(path, command):
  return 'cd ' + path.replace(" ", "\ ") + ' && ' + command

class TestMethodMatcher(object):
  def __init__(self):
    self.matchers = [TestMethodMatcher.UnitTest, TestMethodMatcher.ShouldaTest]
  def find_first_match_in(self, test_file_content):
    for matcher in self.matchers:
      test_name = matcher.find_first_match(test_file_content)
      if test_name:
        return test_name

  class UnitTest(object):
    @staticmethod
    def find_first_match(test_file_content):
      match_obj = re.search('\s?([a-zA-Z_\d]+tset)\s+fed', test_file_content) # 1st search for 'def test_name'
      if match_obj:
        return match_obj.group(1)[::-1]

      match_obj = re.search('\s?[\"\']([a-zA-Z_\"\'\s\d]+)[\"\']\s+tset', test_file_content) # 2nd search for 'test "name"'
      if match_obj:
        test_name = match_obj.group(1)[::-1]
        return "test_%s" % test_name.replace("\"", "\\\"").replace(" ", "_").replace("'", "\\'")

      return None

  class ShouldaTest(object):
    @staticmethod
    def find_first_match(test_file_content):
      match_obj = re.search('\s?(([\"][^\"]*[\"]|[\'][^\']*[\'])\s+dluohs)', test_file_content) # search for 'should "name"'
      if not match_obj:
        return None
      test_name = match_obj.group(1)[::-1]
      return "%s%s%s" % ("/", test_name.replace("should", "").strip(), "/")


class BaseRubyTask(sublime_plugin.TextCommand):
  def load_config(self):
    s = sublime.load_settings("RubyTest.sublime-settings")
    global RUBY_UNIT; RUBY_UNIT = s.get("ruby_unit_exec").split()
    global ERB_EXEC; ERB_EXEC = s.get("erb_exec").split()
    global CUCUMBER_UNIT; CUCUMBER_UNIT = s.get("ruby_cucumber_exec").split()
    global RSPEC_UNIT; RSPEC_UNIT = s.get("ruby_rspec_exec").split()
    global RUBY_UNIT_FOLDER; RUBY_UNIT_FOLDER = s.get("ruby_unit_folder")
    global CUCUMBER_UNIT_FOLDER; CUCUMBER_UNIT_FOLDER = s.get("ruby_cucumber_folder")
    global RSPEC_UNIT_FOLDER; RSPEC_UNIT_FOLDER = s.get("ruby_rspec_folder")
    global USE_SCRATCH; USE_SCRATCH = s.get("ruby_use_scratch")

    if s.get("save_on_run"):
      self.window().run_command("save_all")

  def save_test_run(self, ex, file_name):
    s = sublime.load_settings("RubyTest.last-run")
    s.set("last_test_run", ex)
    s.set("last_test_file", file_name)

    sublime.save_settings("RubyTest.last-run")

  def window(self):
    return self.view.window()

  def reset_test_pannels(self):
    global test_pannels
    test_pannels = {}

  def new_file_window(self):
    global test_pannels
    if not test_pannels:
      test_pannels = self.view.window().new_file()
      test_pannels.set_name("Test Results")
      test_pannels.set_scratch(True)
      test_pannels.set_syntax_file('Packages/Rails/Ruby on Rails.tmLanguage')
    return test_pannels

  def get_test_panel(self):
    global test_pannels
    window = self.window()
    if USE_SCRATCH:
      return self.new_file_window()
    else:
      if window.id() not in test_pannels:
        test_pannels[window.id()] = window.get_output_panel("tests")
        test_pannels[window.id()].set_read_only(True)
      return test_pannels[window.id()]

  def show_tests_panel(self, project_root = None):
    self.clear_test_view(project_root)
    if not USE_SCRATCH:
      self.window().run_command("show_panel", {"panel": "output.tests"})

  def clear_test_view(self, project_root = None):
    if USE_SCRATCH:
      self.reset_test_pannels()
      return
    output_view = self.get_test_panel()
    output_view.set_read_only(False)
    edit = output_view.begin_edit()
    output_view.erase(edit, sublime.Region(0, output_view.size()))
    output_view.end_edit(edit)
    output_view.set_read_only(True)
    output_view.settings().set("result_file_regex", "# ([A-Za-z:0-9_./ ]+rb):([0-9]+)")
    output_view.settings().set("result_base_dir", project_root)

  def append_data(self, proc, data):
    output_view = self.get_test_panel()

    str = unicode(data, errors = "replace")
    str = str.replace('\r\n', '\n').replace('\r', '\n')

    selection_was_at_end = (len(output_view.sel()) == 1
      and output_view.sel()[0]
        == sublime.Region(output_view.size()))
    output_view.set_read_only(False)
    edit = output_view.begin_edit()
    output_view.insert(edit, output_view.size(), str)
    if selection_was_at_end:
      output_view.show(output_view.size())
    output_view.end_edit(edit)
    output_view.set_read_only(True)

  def start_async(self, caption, executable):
    self.is_running = True
    self.proc = AsyncProcess(executable, self)
    StatusProcess(caption, self)

  def update_status(self, msg, progress):
    sublime.status_message(msg + " " + progress)

  class BaseFile(object):
    def __init__(self, file_name): 
      self.folder_name, self.file_name = os.path.split(file_name)
      self.absolute_path = file_name
    def verify_syntax_command(self): return None
    def possible_alternate_files(self): return []
    def run_all_tests_command(self): return None
    def get_project_root(self): return None
    def find_project_root(self, partition_folder):
      project_root, test_folder, file_name = self.absolute_path.partition(partition_folder)
      return project_root
    def run_from_project_root(self, partition_folder, command, options = ""):
      folder_name, test_folder, file_name = self.absolute_path.partition(partition_folder)
      return wrap_in_cd(folder_name, command + " " + partition_folder + file_name + options)
    def relative_file_path(self, partition_folder):
      folder_name, _, file_name = self.absolute_path.partition(partition_folder)
      return partition_folder + file_name

    def get_current_line_number(self, view):
      char_under_cursor = view.sel()[0].a
      return view.rowcol(char_under_cursor)[0] + 1
    def features(self): return []

  class AnonymousFile(BaseFile):
    def __init__(self):
      True

  class RubyFile(BaseFile):
    def verify_syntax_command(self): return RUBY_UNIT + ["-c", self.absolute_path]
    def possible_alternate_files(self): return [self.file_name.replace(".rb", "_spec.rb"), self.file_name.replace(".rb", "_test.rb"), self.file_name.replace(".rb", ".feature")]
    def features(self): return ["verify_syntax", "switch_to_test", "rails_generate", "extract_variable"]

  class UnitFile(RubyFile):
    def possible_alternate_files(self): return [self.file_name.replace("_test.rb", ".rb")]
    def run_all_tests_command(self): return RUBY_UNIT + ["-Itest", self.relative_file_path(RUBY_UNIT_FOLDER)]
    def run_single_test_command(self, view):
      region = view.sel()[0]
      line_region = view.line(region)
      text_string = view.substr(sublime.Region(region.begin() - 2000, line_region.end()))
      text_string = text_string.replace("\n", "\\N")
      text_string = text_string[::-1]
      test_name = TestMethodMatcher().find_first_match_in(text_string)
      if test_name is None:
        sublime.error_message("No test name!")
        return
      return RUBY_UNIT + ["-Itest", self.relative_file_path(RUBY_UNIT_FOLDER), "-n", test_name]
    def features(self): return super(BaseRubyTask.UnitFile, self).features() + ["run_test"]
    def get_project_root(self): return self.find_project_root(RUBY_UNIT_FOLDER)

  class CucumberFile(BaseFile):
    def possible_alternate_files(self): return [self.file_name.replace(".feature", ".rb")]
    def run_all_tests_command(self): return CUCUMBER_UNIT + [self.relative_file_path(CUCUMBER_UNIT_FOLDER)]
    def run_single_test_command(self, view): return CUCUMBER_UNIT + [self.relative_file_path(CUCUMBER_UNIT_FOLDER), "-l" + str(self.get_current_line_number(view))]
    def features(self): return ["run_test"]
    def get_project_root(self): return self.find_project_root(CUCUMBER_UNIT_FOLDER)

  class RSpecFile(RubyFile):
    def possible_alternate_files(self): return [self.file_name.replace("_spec.rb", ".rb")]
    def run_all_tests_command(self): return RSPEC_UNIT + [self.relative_file_path(RSPEC_UNIT_FOLDER)]
    def run_single_test_command(self, view): return RSPEC_UNIT + [self.relative_file_path(RSPEC_UNIT_FOLDER), "-l" + str(self.get_current_line_number(view))]
    def features(self): return super(BaseRubyTask.RSpecFile, self).features() + ["run_test"]
    def get_project_root(self): return self.find_project_root(RSPEC_UNIT_FOLDER)

  class ErbFile(BaseFile):
    def verify_syntax_command(self): return ["sh", "-c", ERB_EXEC.join(" ") + " -xT - " + self.file_name + " | " + RUBY_UNIT + " -c"]
    def can_verify_syntax(self): return True
    def features(self): return ["verify_syntax"]

  def file_type(self, file_name = None):
    file_name = file_name or self.view.file_name()
    if not file_name: return BaseRubyTask.AnonymousFile()
    if re.search('\w+\_test.rb', file_name):
      return BaseRubyTask.UnitFile(file_name)
    elif re.search('\w+\_spec.rb', file_name):
      return BaseRubyTask.RSpecFile(file_name)
    elif re.search('\w+\.feature', file_name):
      return BaseRubyTask.CucumberFile(file_name)
    elif re.search('\w+\.rb', file_name):
      return BaseRubyTask.RubyFile(file_name)
    elif re.search('\w+\.erb', file_name):
      return BaseRubyTask.ErbFile(file_name)
    else:
      return BaseRubyTask.BaseFile(file_name)

class RunSingleRubyTest(BaseRubyTask):
  def is_enabled(self): return 'run_test' in self.file_type().features()
  def run(self, args):
    self.load_config()
    file = self.file_type()
    command = file.run_single_test_command(self.view)
    if command:
      self.save_test_run(command, file.file_name)
      self.view.window().run_command("exec", {
        "cmd": command,
        "working_dir": file.get_project_root(),
        "file_regex": r"([^ ]*\.rb):?(\d*)"
      })
      # self.show_tests_panel(file.get_project_root())
      # self.is_running = True
      # print "command: " + command
      # self.proc = AsyncProcess(command, self)
      # StatusProcess("Starting tests from file " + file.file_name, self)

class RunAllRubyTest(BaseRubyTask):
  def is_enabled(self): return 'run_test' in self.file_type().features()
  def run(self, args):
    self.load_config()
    file = self.file_type(self.view.file_name())
    command = file.run_all_tests_command()
    if command:
      self.save_test_run(command, file.file_name)
      self.view.window().run_command("exec", {
        "cmd": command,
        "working_dir": file.get_project_root(),
        "file_regex": r"([^ ]*\.rb):?(\d*)"
      })
    else:
      sublime.error_message("Only *_test.rb, *_spec.rb, *.feature files supported!")


class RunLastRubyTest(BaseRubyTask):
  def load_last_run(self):
    self.load_config()
    s = sublime.load_settings("RubyTest.last-run")
    global LAST_TEST_RUN; LAST_TEST_RUN = s.get("last_test_run")
    global LAST_TEST_FILE; LAST_TEST_FILE = s.get("last_test_file")

  def run(self, args):
    self.load_last_run()
    file = self.file_type(LAST_TEST_FILE)
    self.view.window().run_command("exec", {
      "cmd": LAST_TEST_RUN,
      "working_dir": file.get_project_root(),
      "file_regex": r"([^ ]*\.rb):?(\d*)"
    })

class VerifyRubyFile(BaseRubyTask):
  def is_enabled(self): return 'verify_syntax' in self.file_type().features()
  def run(self, args):
    self.load_config()
    file = self.file_type()
    command = file.verify_syntax_command()
    if command:
      self.view.window().run_command("exec", {
        "cmd": command,
        "working_dir": file.get_project_root(),
        "file_regex": r"([^ ]*\.rb):?(\d*)"
      })
    else:
      sublime.error_message("Only .rb or .erb files supported!")

class SwitchBetweenCodeAndTest(BaseRubyTask):
  def is_enabled(self): return 'switch_to_test' in self.file_type().features()
  def run(self, args, split_view):
    possible_alternates = self.file_type().possible_alternate_files()
    alternates = self.project_files(lambda file: file in possible_alternates)
    if alternates:
      if split_view:
        self.window().run_command('set_layout', {
                              "cols": [0.0, 0.5, 1.0],
                              "rows": [0.0, 1.0],
                              "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]
                          })
        self.window().focus_group(1)
      if len(alternates) == 1:
        self.window().open_file(alternates.pop())
      else:
        callback = functools.partial(self.on_selected, alternates)
        self.window().show_quick_panel(alternates, callback)
    else:
      sublime.error_message("could not find " + str(possible_alternates))

  def on_selected(self, alternates, index):
    if index == -1:
      return

    self.window().open_file(alternates[index])

  def walk(self, directory, ignored_directories = []):
    ignored_directories = ['.git', 'vendor']  # Move this into config
    for dir, dirnames, files in os.walk(directory):
      dirnames[:] = [dirname for dirname in dirnames if dirname not in ignored_directories]
      yield dir, dirnames, files

  def project_files(self, file_matcher):
    directories = self.window().folders()
    return [os.path.join(dirname, file) for directory in directories for dirname, _, files in self.walk(directory) for file in filter(file_matcher, files)]

class RubyRunShell(BaseRubyTask):
  def run(self, args, command, caption = "Running shell command"):
    self.show_tests_panel()
    self.start_async(caption, wrap_in_cd(self.window().folders()[0], command))

class RubyRailsGenerate(BaseRubyTask):
  def is_enabled(self): return 'rails_generate' in self.file_type().features()

  def run(self, args, type = "migration"):
    self.window().show_input_panel("rails generate", type + " ", lambda s: self.generate(s), None, None)

  def generate(self, argument):
    command = "rails generate " + argument
    self.view.run_command("ruby_run_shell", {"command": command, "caption": "Generating" + argument})

class RubyExtractVariable(BaseRubyTask):
  def is_enabled(self): return 'extract_variable' in self.file_type().features()
  def run(self, args):
    for selection in self.view.sel():
      self.window().show_input_panel("Variable Name: ", '', lambda name: self.generate(selection, name), None, None)

  def generate(self, selection, name):
    extracted = self.view.substr(selection)
    line = self.view.line(selection)
    white_space = re.match("\s*", self.view.substr(line)).group()
    edit = self.view.begin_edit()
    try:
      self.view.replace(edit, selection, name)
      self.view.insert(edit, line.begin(), white_space + name + " = " + extracted + "\n")
    finally:
      self.view.end_edit(edit)
