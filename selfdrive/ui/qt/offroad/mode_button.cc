#include "selfdrive/ui/qt/offroad/mode_button.h"
#include <QHBoxLayout>
#include <QVBoxLayout>
#include <QPainter>
#include <QPainterPath>
#include <QMouseEvent>

const int HEADER_FONT_SIZE = 64;
const int DESCRIPTION_FONT_SIZE = 32;
const int TOGGLE_BUTTON_SIZE = 32;
const int BUTTON_HEIGHT = 120;
const int BUTTON_PADDING = 20;
const int AUTO_COLLAPSE_TIMEOUT = 10000; // 10 seconds

ModeButton::ModeButton(QWidget* parent) : QWidget(parent), isExpanded(false), isToggledOn(false) {
    setFixedHeight(BUTTON_HEIGHT);

    QHBoxLayout *mainLayout = new QHBoxLayout(this);
    mainLayout->setContentsMargins(BUTTON_PADDING, 0, BUTTON_PADDING, 0);
    mainLayout->setSpacing(0);

    headerText = new QLabel(this);
    headerText->setStyleSheet(QString("font-size: %1px; font-weight: bold; color: #909090;").arg(HEADER_FONT_SIZE));
    mainLayout->addWidget(headerText, 1);

    toggleButton = new QPushButton(this);
    toggleButton->setFixedSize(TOGGLE_BUTTON_SIZE, TOGGLE_BUTTON_SIZE);
    toggleButton->setStyleSheet("border: 2px solid white; border-radius: 16px; background-color: transparent;");
    connect(toggleButton, &QPushButton::clicked, this, &ModeButton::onToggleClicked);
    mainLayout->addWidget(toggleButton);

    descriptionText = new QLabel(this);
    descriptionText->setStyleSheet(QString("font-size: %1px; font-weight: medium; color: white;").arg(DESCRIPTION_FONT_SIZE));
    descriptionText->setWordWrap(true);
    descriptionText->setVisible(false);

    collapseTimer = new QTimer(this);
    collapseTimer->setSingleShot(true);
    connect(collapseTimer, &QTimer::timeout, this, &ModeButton::autoCollapse);

    setLayout(mainLayout);
}

void ModeButton::setModeText(const QString &text) {
    modeText = text;
    headerText->setText(text);
}

void ModeButton::setDescriptionText(const QString &text) {
    description = text;
    descriptionText->setText(text);
}

void ModeButton::setGradient(const QColor &startColor, const QColor &endColor) {
    gradientStart = startColor;
    gradientEnd = endColor;
    update();
}

void ModeButton::setToggleEnabled(bool enabled) {
    isToggledOn = enabled;
    toggleButton->setStyleSheet(isToggledOn ?
        "border: 2px solid white; border-radius: 16px; background-color: #00ff00;" :
        "border: 2px solid white; border-radius: 16px; background-color: transparent;");
    emit modeToggled(modeText, isToggledOn);
}

void ModeButton::onButtonClicked() {
    isExpanded = !isExpanded;
    setFixedHeight(isExpanded ? BUTTON_HEIGHT * 2 : BUTTON_HEIGHT);
    descriptionText->setVisible(isExpanded);
    headerText->setStyleSheet(QString("font-size: %1px; font-weight: bold; color: %2;")
        .arg(isExpanded ? HEADER_FONT_SIZE - 16 : HEADER_FONT_SIZE)
        .arg(isExpanded ? "white" : "#909090"));

    if (isExpanded) {
        collapseTimer->start(AUTO_COLLAPSE_TIMEOUT);
    } else {
        collapseTimer->stop();
    }

    emit descriptionToggled(isExpanded);
}

void ModeButton::onToggleClicked() {
    setToggleEnabled(!isToggledOn);
}

void ModeButton::autoCollapse() {
    if (isExpanded) {
        onButtonClicked();
    }
}

void ModeButton::paintEvent(QPaintEvent *event) {
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing);

    QLinearGradient gradient(rect().topLeft(), rect().bottomRight());
    gradient.setColorAt(0, gradientStart);
    gradient.setColorAt(1, gradientEnd);
    painter.fillRect(rect(), gradient);

    QWidget::paintEvent(event);
}

void ModeButton::mousePressEvent(QMouseEvent *event) {
    if (event->x() < width() - TOGGLE_BUTTON_SIZE - BUTTON_PADDING) {
        onButtonClicked();
    }
    QWidget::mousePressEvent(event);
}