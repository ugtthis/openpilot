#include "selfdrive/ui/qt/offroad/mode_button.h"
#include <QHBoxLayout>
#include <QVBoxLayout>
#include <QPainter>
#include <QPainterPath>
#include <QStyle>
#include <QVariant>

ModeButton::ModeButton(QWidget* parent) : QWidget(parent), isExpanded(false), isToggledOn(false) {
  QVBoxLayout *mainLayout = new QVBoxLayout(this);
  mainLayout->setContentsMargins(0, 0, 0, 0);
  mainLayout->setSpacing(0);

  QHBoxLayout *headerLayout = new QHBoxLayout();
  headerText = new QLabel(this);
  headerText->setStyleSheet("font-size: 64px; font-weight: bold; color: rgb(144, 144, 144);");
  headerLayout->addWidget(headerText);

  toggleButton = new QPushButton(this);
  toggleButton->setFixedSize(32, 32);
  toggleButton->setStyleSheet("border: 1px solid black; border-radius: 16px;");
  connect(toggleButton, &QPushButton::clicked, this, &ModeButton::onToggleClicked);
  headerLayout->addWidget(toggleButton);

  mainLayout->addLayout(headerLayout);

  descriptionText = new QLabel(this);
  descriptionText->setStyleSheet("font-size: 32px; font-weight: medium; color: rgb(144, 144, 144);");
  descriptionText->setVisible(false);
  mainLayout->addWidget(descriptionText);

  setLayout(mainLayout);

  collapseTimer = new QTimer(this);
  collapseTimer->setSingleShot(true);
  connect(collapseTimer, &QTimer::timeout, this, &ModeButton::autoCollapse);
}

void ModeButton::setModeText(const QString &text) {
  modeText = text;
  headerText->setText(text);
}

void ModeButton::setDescriptionText(const QString &text) {
  description = text;
  descriptionText->setText(text);
}

void ModeButton::setGradient(const QString &gradient) {
  setProperty("gradient", QVariant(gradient));
}

void ModeButton::toggleMode() {
  isToggledOn = !isToggledOn;
  toggleButton->setStyleSheet(isToggledOn ? "background-color: neon green;" : "background-color: none;");
  emit modeToggled(modeText, isToggledOn);
}

void ModeButton::expandDescription() {
  isExpanded = true;
  headerText->setStyleSheet("font-size: 64px; font-weight: bold; color: white;");
  descriptionText->setVisible(true);
  collapseTimer->start(10000); // 10 seconds
}

void ModeButton::collapseDescription() {
  isExpanded = false;
  headerText->setStyleSheet("font-size: 64px; font-weight: bold; color: rgb(144, 144, 144);");
  descriptionText->setVisible(false);
}

void ModeButton::onButtonClicked() {
  if (isExpanded) {
    collapseDescription();
  } else {
    expandDescription();
  }
}

void ModeButton::onToggleClicked() {
  toggleMode();
}

void ModeButton::autoCollapse() {
  if (isExpanded) {
    collapseDescription();
  }
}

void ModeButton::paintEvent(QPaintEvent *event) {
  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);

  QString gradient = property("gradient").toString();
  if (!gradient.isEmpty()) {
    QLinearGradient linearGrad(rect().topLeft(), rect().bottomRight());
    linearGrad.setColorAt(0, QColor(20, 255, 171, 255));
    linearGrad.setColorAt(1, QColor(35, 149, 255, 255));
    painter.fillRect(rect(), linearGrad);
  }
}