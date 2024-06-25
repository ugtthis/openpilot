#include "selfdrive/ui/qt/widgets/driver_alert_cluster.h"
#include <QPainter>
#include <QPainterPath>
#include <algorithm>
#include "selfdrive/ui/qt/util.h"

DriverAlertCluster::DriverAlertCluster(UIState *ui_state, QWidget *parent)
    : QWidget(parent), ui_state(ui_state) {
  setFixedSize(640, 360);
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
      icons[alertBar.iconName] = QIcon(pixmap);
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

void DriverAlertCluster::updateState(const UIState &s) {
  const auto& disengagePreds = getDisengagePredictions(*s.sm);
  alertBars[0].alertLevel = calculateAlertLevel(disengagePreds.getSteerOverrideProbs());
  alertBars[1].alertLevel = calculateAlertLevel(disengagePreds.getBrakeDisengageProbs());
  alertBars[2].alertLevel = calculateAlertLevel(disengagePreds.getGasDisengageProbs());
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
  if (max_prob > 0.05) return 1;
  return 0;
}

DriverAlertCluster::AlertProperties DriverAlertCluster::getAlertProperties(int alertLevel) {
  AlertProperties properties;

  // Initialize properties with default values
  properties.borderColor = QColor(0, 0, 0);
  properties.fillColor = QColor(11, 16, 22);
  properties.iconColor = QColor(254, 255, 255);
  properties.textColor = QColor(254, 255, 255);
  properties.borderWidth = 4;
  properties.shadowColor = QColor(0, 0, 0);
  properties.shadowOpacity = 0.4f;
  properties.blurRadius = 10;
  properties.circleColors.fill(QColor(118, 117, 117));

  switch (alertLevel) {
    case 0: // Disabled
      properties.borderColor = QColor(0, 0, 0, 50);
      properties.fillColor = QColor(11, 16, 22, 50);
      properties.iconColor = QColor(118, 117, 117, 50);
      properties.textColor = QColor(118, 117, 117, 50);
      properties.shadowColor = QColor(0, 0, 0, 50);
      properties.circleColors.fill(QColor(118, 117, 117, 50));
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
      properties.borderColor = QColor(239, 255, 54);
      properties.iconColor = QColor(254, 255, 255);
      properties.shadowColor = QColor(239, 255, 54);
      properties.blurRadius = 20;
      properties.circleColors[0] = QColor(8, 64, 80);
      properties.circleColors[1] = QColor(8, 64, 80);
      properties.circleColors[2] = QColor(67, 71, 21);
      properties.circleColors[3] = QColor(67, 71, 21);
      properties.circleColors[4] = QColor(239, 255, 54);
      break;

    case 6: // High alert level 1
    case 7: // High alert level 2
      properties.borderColor = QColor(255, 60, 70);
      properties.iconColor = QColor(254, 255, 255);
      properties.shadowColor = QColor(255, 60, 70);
      properties.blurRadius = 20;
      properties.circleColors[0] = QColor(8, 64, 80);
      properties.circleColors[1] = QColor(8, 64, 80);
      properties.circleColors[2] = QColor(67, 71, 21);
      properties.circleColors[3] = QColor(67, 71, 21);
      properties.circleColors[4] = QColor(67, 71, 21);
      properties.circleColors[5] = QColor(255, 60, 70);
      if (alertLevel == 7) properties.circleColors[6] = QColor(255, 60, 70);
      break;
  }

  return properties;
}

QRadialGradient DriverAlertCluster::createGlowGradient(const QRectF &rect, const QColor &color) const {
  QRadialGradient gradient(rect.center(), rect.width() / 2);
  QColor glowColor = color;
  glowColor.setAlpha(100);  // Adjust alpha for glow intensity
  gradient.setColorAt(0, glowColor);
  gradient.setColorAt(0.5, QColor(glowColor.red(), glowColor.green(), glowColor.blue(), 50));
  gradient.setColorAt(1, Qt::transparent);
  return gradient;
}

bool DriverAlertCluster::renderIcon(QPainter &painter, const QString &iconName, const QRect &rect, const QColor &color) {
  auto it = icons.find(iconName);
  if (it != icons.end()) {
    painter.save();
    QPixmap pixmap = it.value().pixmap(rect.size());
    QPainter pixmapPainter(&pixmap);
    pixmapPainter.setCompositionMode(QPainter::CompositionMode_SourceIn);
    pixmapPainter.fillRect(pixmap.rect(), color);
    pixmapPainter.end();
    painter.drawPixmap(rect, pixmap);
    painter.restore();
    return true;
  }
  return false;
}

void DriverAlertCluster::drawRoundedRect(QPainter &painter, const QRectF &rect, qreal xRadius, qreal yRadius) {
  QPainterPath path;
  path.addRoundedRect(rect, xRadius, yRadius);
  painter.drawPath(path);
}

void DriverAlertCluster::drawAlertBar(QPainter &painter, const AlertBar &alertBar, int yOffset) {
  const AlertProperties &properties = cachedAlertProperties[alertBar.alertLevel];
  QRectF barRect(HORIZONTAL_PADDING, yOffset, BAR_WIDTH, BAR_HEIGHT);

  // Draw glow effect for Medium Alert (3rd level) and High Alert states
  if (alertBar.alertLevel >= 5) {
    painter.save();
    painter.setPen(Qt::NoPen);
    QRadialGradient glowGradient = createGlowGradient(barRect.adjusted(-20, -20, 20, 20), properties.borderColor);
    painter.setBrush(glowGradient);
    drawRoundedRect(painter, barRect.adjusted(-20, -20, 20, 20), CORNER_RADIUS, CORNER_RADIUS);
    painter.restore();
  }

  // Draw background
  painter.setPen(Qt::NoPen);
  painter.setBrush(properties.fillColor);
  drawRoundedRect(painter, barRect, CORNER_RADIUS, CORNER_RADIUS);

  // Draw border
  painter.setPen(QPen(properties.borderColor, properties.borderWidth));
  painter.setBrush(Qt::NoBrush);
  drawRoundedRect(painter, barRect, CORNER_RADIUS, CORNER_RADIUS);

  // Draw text
  painter.setPen(properties.textColor);
  painter.setFont(QFont("Inter", FONT_SIZE, QFont::DemiBold));
  painter.drawText(barRect.adjusted(20 + ICON_SIZE + 10, 0, -20, 0), Qt::AlignVCenter | Qt::AlignLeft, alertBar.label);

  // Draw icon
  QRect iconRect(barRect.left() + 20, barRect.top() + (barRect.height() - ICON_SIZE) / 2, ICON_SIZE, ICON_SIZE);
  if (!renderIcon(painter, alertBar.iconName, iconRect, properties.iconColor)) {
    qWarning() << "Failed to render icon:" << alertBar.iconName;
  }

  // Draw circles
  int circleXOffset = barRect.right() - 280;
  for (int i = 0; i < NUM_CIRCLES; ++i) {
    painter.setBrush(properties.circleColors[i]);
    painter.setPen(Qt::NoPen);
    QPointF circleCenter(circleXOffset + (i * 35) + CIRCLE_SIZE / 2,
                         barRect.y() + (barRect.height() / 2));
    painter.drawEllipse(circleCenter, CIRCLE_SIZE / 2, CIRCLE_SIZE / 2);
  }
}

void DriverAlertCluster::paintEvent(QPaintEvent *event) {
  Q_UNUSED(event);

  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);

  // Clear the background
  painter.fillRect(rect(), Qt::transparent);

  // Draw each alert bar
  for (size_t i = 0; i < alertBars.size(); ++i) {
    int yOffset = VERTICAL_PADDING + i * (BAR_HEIGHT + VERTICAL_PADDING);
    drawAlertBar(painter, alertBars[i], yOffset);
  }
}