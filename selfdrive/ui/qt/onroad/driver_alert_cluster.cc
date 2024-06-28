#include "selfdrive/ui/qt/onroad/driver_alert_cluster.h"

#include <QPainter>
#include <QPainterPath>
#include <algorithm>
#include <cmath>
#include <iostream>
#include <string>
#include <iomanip>

#include "selfdrive/ui/qt/util.h"


DriverAlertCluster::DriverAlertCluster(UIState *ui_state, QWidget *parent)
    : QWidget(parent), ui_state(ui_state) {
  setFixedSize(720, 360); // BEFORE 640 / 360
  initializeAlertBars();
  if (!loadIcons()) {
    qWarning() << "Failed to load one or more icons for DriverAlertCluster";
  }

  updateTimer = new QTimer(this);
  connect(updateTimer, &QTimer::timeout, this, QOverload<>::of(&DriverAlertCluster::update));
  updateTimer->start(1000 / 20); // Update at 20Hz

  setContentsMargins(0, 0, 0, 0);

  // Pre-calculate alert properties for performance
  for (int i = 0; i < NUM_ALERT_LEVELS; ++i) {
    cachedAlertProperties[i] = getAlertProperties(i);
  }
}

DriverAlertCluster::~DriverAlertCluster() {
  delete updateTimer;
}

void DriverAlertCluster::initializeAlertBars() {
  alertBars = {{
    {"Steering", 0, "steering_dac"},
    {"Brake", 0, "brake_dac"},
    {"Gas", 0, "gas_dac"}
  }};
}

bool DriverAlertCluster::loadIcons() {
  bool allLoaded = true;
  for (const auto &alertBar : alertBars) {
    QString path = QString("../assets/icons/%1.svg").arg(alertBar.iconName);
    QPixmap pixmap = loadPixmap(path);
    if (!pixmap.isNull()) {
      IconInfo info;
      info.icon = QIcon(pixmap);
      info.aspectRatio = static_cast<qreal>(pixmap.width()) / pixmap.height();
      iconInfo[alertBar.iconName] = info;
    } else {
      qWarning() << "Failed to load icon:" << path;
      allLoaded = false;
    }
  }
  return allLoaded;
}

cereal::ModelDataV2::DisengagePredictions::Reader DriverAlertCluster::getDisengagePredictions(const SubMaster &sm) const {
  return sm["modelV2"].getModelV2().getMeta().getDisengagePredictions();
}

void DriverAlertCluster::printAlertLevels() const {
    std::cout << "\n\n***** DRIVER ALERT CLUSTER DEBUG OUTPUT *****" << std::endl;
    std::cout << "Alert Levels and Probabilities:" << std::endl;
    for (const auto& bar : alertBars) {
        std::cout << bar.label.toStdString() << ": "
                  << "Level " << bar.alertLevel
                  << " (Probability: " << std::fixed << std::setprecision(4) << bar.probability << ")"
                  << std::endl;
    }
    std::cout << "*********************************************\n\n" << std::endl;
    std::cout.flush();  // Ensure output is flushed to console
}

void DriverAlertCluster::updateState(const UIState &s) {
  const auto& disengagePreds = getDisengagePredictions(*s.sm);

  auto updateAlertBar = [this](int index, const capnp::List<float>::Reader& probs) {
    float max_prob = *std::max_element(probs.begin(), probs.end());
    alertBars[index].alertLevel = calculateAlertLevel(probs);
    alertBars[index].probability = max_prob;
  };

  updateAlertBar(0, disengagePreds.getSteerOverrideProbs());
  updateAlertBar(1, disengagePreds.getBrakeDisengageProbs());
  updateAlertBar(2, disengagePreds.getGasDisengageProbs());

  printAlertLevels();
  update();
}

int DriverAlertCluster::calculateAlertLevel(const capnp::List<float>::Reader& probs) {
  float max_prob = *std::max_element(probs.begin(), probs.end());
  if (max_prob > 0.8) return 7;
  if (max_prob > 0.6) return 6;
  if (max_prob > 0.4) return 5;
  if (max_prob > 0.3) return 4;
  if (max_prob > 0.2) return 3;
  if (max_prob > 0.1) return 2;
  if (max_prob > 0.001) return 1; // Lowered this from 0.5 to get it not to be in disengaged state
  return 0;
}

DriverAlertCluster::AlertProperties DriverAlertCluster::getAlertProperties(int alertLevel) {
  AlertProperties properties;

  // Initialize properties with default values
  properties.borderColor = QColor(0, 0, 0);
  properties.fillColor = QColor(11, 16, 22);
  properties.iconColor = QColor(254, 255, 255);
  properties.textColor = QColor(254, 255, 255);
  properties.borderWidth = 10;
  properties.circleColors.fill(QColor(118, 117, 117));

  switch (alertLevel) {
    case 0: // Disabled
      properties.borderColor = QColor(0, 0, 0, 0);
      properties.fillColor = QColor(11, 16, 22, 50);
      properties.iconColor = QColor(118, 117, 117, 70);
      properties.textColor = QColor(118, 117, 117, 70);
      properties.circleColors.fill(QColor(118, 117, 117, 70));
      break;

    case 1: // Low alert level 1
    case 2: // Low alert level 2
      properties.iconColor = QColor(0, 209, 255);
      properties.circleColors[0] = QColor(0, 209, 255);
      if (alertLevel == 2) properties.circleColors[1] = QColor(0, 209, 255);
      break;

    case 3: // Medium alert level 1
    case 4: // Medium alert level 2
      properties.iconColor = QColor(239, 255, 54);
      properties.circleColors[0] = QColor(8, 64, 80);
      properties.circleColors[1] = QColor(8, 64, 80);
      properties.circleColors[2] = QColor(239, 255, 54);
      if (alertLevel == 4) properties.circleColors[3] = QColor(239, 255, 54);
      break;

    case 5: // Medium alert level 3
    case 6: // High alert level 1
    case 7: // High alert level 2
      properties.borderColor = (alertLevel == 5) ? QColor(239, 255, 54) : QColor(255, 60, 70);
      properties.fillColor = (alertLevel >= 6) ? QColor(255, 60, 70, 80) : QColor(11, 16, 22);
      properties.iconColor = QColor(254, 255, 255);
      properties.circleColors[0] = QColor(8, 64, 80);
      properties.circleColors[1] = QColor(8, 64, 80);
      properties.circleColors[2] = QColor(67, 71, 21);
      properties.circleColors[3] = QColor(67, 71, 21);
      properties.circleColors[4] = (alertLevel >= 6) ? QColor(67, 71, 21) : QColor(239, 255, 54);
      if (alertLevel >= 6) properties.circleColors[5] = QColor(255, 60, 70);
      if (alertLevel == 7) properties.circleColors[6] = QColor(255, 60, 70);
      break;
  }

  return properties;
}

bool DriverAlertCluster::renderIcon(QPainter &painter, const QString &iconName, const QRectF &rect, const QColor &color) {
  auto it = iconInfo.find(iconName);
  if (it != iconInfo.end()) {
    painter.save();
    painter.setRenderHint(QPainter::SmoothPixmapTransform, true);

    qreal availableAspectRatio = rect.width() / rect.height();
    QSizeF iconSize;

    if (it.value().aspectRatio > availableAspectRatio) {
      // Icon is wider than the available space
      iconSize = QSizeF(rect.width(), rect.width() / it.value().aspectRatio);
    } else {
      // Icon is taller than or equal to the available space
      iconSize = QSizeF(rect.height() * it.value().aspectRatio, rect.height());
    }

    QRectF iconRect(
      rect.x() + (rect.width() - iconSize.width()) / 2,
      rect.y() + (rect.height() - iconSize.height()) / 2,
      iconSize.width(),
      iconSize.height());

    QPixmap pixmap = it.value().icon.pixmap(iconSize.toSize());

    QPainter pixmapPainter(&pixmap);
    pixmapPainter.setCompositionMode(QPainter::CompositionMode_SourceIn);
    pixmapPainter.fillRect(pixmap.rect(), color);
    pixmapPainter.end();

    painter.drawPixmap(iconRect.toRect(), pixmap);
    painter.restore();
    return true;
  }
  return false;
}

void DriverAlertCluster::drawRoundedRect(QPainter &painter, const QRectF &rect, qreal xRadius, qreal yRadius) {
  painter.save();
  painter.setRenderHint(QPainter::Antialiasing, true);

  QPainterPath path;
  path.addRoundedRect(rect, xRadius, yRadius);
  painter.drawPath(path);

  painter.restore();
}

void DriverAlertCluster::drawAlertBar(QPainter &painter, const AlertBar &alertBar, int yOffset) {
  painter.save();
  painter.setRenderHint(QPainter::Antialiasing, true);
  painter.setRenderHint(QPainter::SmoothPixmapTransform, true);

  const AlertProperties &properties = cachedAlertProperties[alertBar.alertLevel];

  QRectF barRect(HORIZONTAL_PADDING, yOffset, BAR_WIDTH, BAR_HEIGHT);

  // Draw background
  painter.setPen(QPen(properties.borderColor, properties.borderWidth));
  painter.setBrush(properties.fillColor);
  drawRoundedRect(painter, barRect, CORNER_RADIUS, CORNER_RADIUS);

  // Draw border
  painter.setPen(QPen(properties.borderColor, properties.borderWidth));
  painter.setBrush(Qt::NoBrush);
  drawRoundedRect(painter, barRect, CORNER_RADIUS, CORNER_RADIUS);

  // Calculate rectangles for different areas of the alert bar
  QRectF iconRect(barRect.left() + ICON_PADDING,
                  barRect.top() + (barRect.height() - ICON_SIZE) / 2,
                  ICON_SIZE, ICON_SIZE);

  QRectF textRect = barRect.adjusted(ICON_PADDING + ICON_SIZE + TEXT_ICON_SPACING,
                                     0,
                                     -CIRCLE_AREA_WIDTH - CIRCLE_RIGHT_MARGIN,
                                     0);
  QRectF circleAreaRect(barRect.right() - CIRCLE_AREA_WIDTH - CIRCLE_RIGHT_MARGIN,
                        barRect.top(),
                        CIRCLE_AREA_WIDTH,
                        barRect.height());

  // Draw icon
  if (!renderIcon(painter, alertBar.iconName, iconRect, properties.iconColor)) {
    qWarning() << "Failed to render icon:" << alertBar.iconName;
  }

  // Draw text
  painter.setPen(properties.textColor);
  painter.setFont(QFont("Inter", FONT_SIZE, QFont::DemiBold));
  painter.drawText(textRect, Qt::AlignVCenter | Qt::AlignLeft, alertBar.label);

  // Draw circles
  int circleSpacing = (CIRCLE_AREA_WIDTH - (NUM_CIRCLES * CIRCLE_SIZE)) / (NUM_CIRCLES + 1);
  for (int i = 0; i < NUM_CIRCLES; ++i) {
    painter.setBrush(properties.circleColors[i]);
    painter.setPen(Qt::NoPen);
    qreal circleX = circleAreaRect.left() + circleSpacing + (i * (CIRCLE_SIZE + circleSpacing));
    qreal circleY = circleAreaRect.top() + (circleAreaRect.height() - CIRCLE_SIZE) / 2;
    painter.drawEllipse(QRectF(circleX, circleY, CIRCLE_SIZE, CIRCLE_SIZE));
  }
  painter.restore();
}


void DriverAlertCluster::paintEvent(QPaintEvent *event) {
  Q_UNUSED(event);

  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);
  painter.setRenderHint(QPainter::SmoothPixmapTransform, true);
  painter.setRenderHint(QPainter::TextAntialiasing, true);

  // REMOVE PR
  // // DRAWS BORDER - VISUAL
  // painter.setPen(QPen(Qt::red, 2));  // Red color, 2px width
  // painter.drawRect(rect().adjusted(1, 1, -1, -1));  // Adjust to keep border inside the widget

  // Clear the background
  painter.fillRect(rect(), Qt::transparent);

  // Draw each alert bar
  for (size_t i = 0; i < alertBars.size(); ++i) {
    int yOffset = VERTICAL_PADDING + i * (BAR_HEIGHT + VERTICAL_PADDING);
    drawAlertBar(painter, alertBars[i], yOffset);
  }
}