#include "experimental_mode_toggle.h"

#include <QPainter>
#include <QPainterPath>
#include <QHBoxLayout>
#include <QLabel>
#include <QSizePolicy>
#include <QDebug>
#include <cstdio>
#include <fstream>
#include <ctime>
#include <iostream>
#include <QThread>

ExperimentalModeToggle::ExperimentalModeToggle(QWidget *parent) : QPushButton(parent) {
  // Set fixed size
  setFixedSize(750, 125);

  // Create a horizontal layout
  QHBoxLayout *main_layout = new QHBoxLayout(this);
  main_layout->setContentsMargins(30, 0, 30, 0);

  // Create and add a label for the text
  mode_label = new QLabel(this);
  mode_label->setStyleSheet(R"(
    font-size: 45px;
    font-weight: 300;
    font-family: JetBrainsMono;
    color: #000000;
  )");
  main_layout->addWidget(mode_label, 1, Qt::AlignLeft);

  // Set the layout
  setLayout(main_layout);

  // Remove the default button styling
  setFlat(true);
  setStyleSheet("QPushButton { background-color: transparent; border: none; }");

  // Connect the clicked signal to the toggle function
  connect(this, &QPushButton::clicked, this, &ExperimentalModeToggle::toggleExperimentalMode);
  printf("Button connected to toggleExperimentalMode function\n");
  fflush(stdout);

  // Initialize the button state
  updateState();
}

void ExperimentalModeToggle::toggleExperimentalMode() {
  bool previous_mode = experimental_mode;
  experimental_mode = !experimental_mode;
  params.putBool("ExperimentalMode", experimental_mode);

  std::cout << "\n*** EXPERIMENTAL MODE TOGGLED ***\n";
  std::cout << "Previous value: " << (previous_mode ? "true" : "false") << "\n";
  std::cout << "New value: " << (experimental_mode ? "true" : "false") << "\n";
  std::cout << "Stored value: " << (params.getBool("ExperimentalMode") ? "true" : "false") << "\n";
  std::cout << std::flush;

  updateState();

  // Check OpenpilotEnabledToggle
  std::string openpilotEnabled = params.get("OpenpilotEnabledToggle");
  std::cout << "OpenpilotEnabledToggle: " << openpilotEnabled << "\n";

  // Attempt to toggle OpenpilotEnabledToggle
  bool currentState = (openpilotEnabled == "1");
  params.putBool("OpenpilotEnabledToggle", !currentState);
  std::cout << "Attempting to toggle OpenpilotEnabledToggle to: " << (!currentState ? "1" : "0") << "\n";

  // Check again after toggling
  openpilotEnabled = params.get("OpenpilotEnabledToggle");
  std::cout << "OpenpilotEnabledToggle after toggle attempt: " << openpilotEnabled << "\n";

  // Print all parameters
  std::cout << "\nAll parameters:\n";
  std::map<std::string, std::string> all_params = params.readAll();
  for (const auto& param : all_params) {
    std::cout << param.first << ": " << param.second << "\n";
  }

  std::cout << "\nTotal number of parameters: " << all_params.size() << "\n";
  std::cout << std::endl;  // Flush the output
}

void ExperimentalModeToggle::updateState() {
  experimental_mode = params.getBool("ExperimentalMode");
  mode_label->setText(experimental_mode ? tr("EXPERIMENTAL MODE ENABLED") : tr("EXPERIMENTAL MODE off"));
  update();
}

void ExperimentalModeToggle::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);

  QPainterPath path;
  path.addRoundedRect(rect(), 10, 10);

  // gradient
  bool pressed = isDown();
  QLinearGradient gradient(rect().left(), 0, rect().right(), 0);
  if (experimental_mode) {
    gradient.setColorAt(0, QColor(255, 155, 63, pressed ? 0xcc : 0xff));
    gradient.setColorAt(1, QColor(219, 56, 34, pressed ? 0xcc : 0xff));
  } else {
    gradient.setColorAt(0, QColor(128, 128, 128, pressed ? 0xcc : 0xff));
    gradient.setColorAt(1, QColor(80, 80, 80, pressed ? 0xcc : 0xff));
  }
  p.fillPath(path, gradient);
}