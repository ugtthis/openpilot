#include "selfdrive/ui/qt/widgets/driver_alert_dial.h"
#include <algorithm>
#include <QPainter>
#include <QPen>
#include <QBrush>
#include <QPainterPath>
#include <QRectF>
#include <cmath>

// Implements createRadialGradient function
QRadialGradient DriverAlertDial::createRadialGradient(const QPointF &center,
                                                      const QColor &color,
                                                      int radius,
                                                      int blur_radius,
                                                      int opacity,
                                                      int x_offset,
                                                      int y_offset) {
  QRadialGradient gradient(center + QPointF(x_offset, y_offset), radius + blur_radius);

  QColor intenseColor = color;
  intenseColor.setAlpha(opacity);

  QColor midColor = color;
  midColor.setAlpha(opacity * 0.8);

  QColor transparentColor = color;
  transparentColor.setAlpha(0);

  gradient.setColorAt(0, intenseColor);
  gradient.setColorAt(0.7, midColor);
  gradient.setColorAt(1, transparentColor);

  return gradient;
}

// Constructor: This initializes the widgets properties
DriverAlertDial::DriverAlertDial(QWidget *parent) : QWidget(parent),
    confidence(cereal::ModelDataV2::ConfidenceClass::GREEN),
    steering_torque(0.0),
    brake_pressure(0.0),
    acceleration(0.0),
    is_engaged(false) {

  setFixedSize(390, 390); //Set the widget size
}

void DriverAlertDial::setEngagedStatus(bool engaged) {
  is_engaged = engaged;
  update();
}

// Updates the internal state of widget
void DriverAlertDial::updateState(cereal::ModelDataV2::ConfidenceClass conf,
                                  float steer_torque,
                                  float brake,
                                  float accel,
                                  bool engaged) {
  confidence = conf;
  steering_torque = steer_torque;
  brake_pressure = brake;
  acceleration = accel;
  is_engaged = engaged;
  update(); //this requests a repaint
}

// Helper function to draw a circle with a border
void DriverAlertDial::drawCircle(QPainter &painter,
                                  const QPointF &center,
                                  const QColor &fill_color,
                                  const QColor &border_color,
                                  const QColor &shadow_color,
                                  int border_thickness,
                                  int radius,
                                  int shadow_blur_radius,
                                  int shadow_opacity,
                                  int shadow_x,
                                  int shadow_y) {
  // Draws shadow IF applicable
  if (shadow_blur_radius > 0 && shadow_opacity > 0) {
    QRadialGradient gradient = createRadialGradient(center,
                                                    shadow_color,
                                                    radius,
                                                    shadow_blur_radius,
                                                    shadow_opacity,
                                                    shadow_x,
                                                    shadow_y);

    painter.setPen(Qt::NoPen);
    painter.setBrush(QBrush(shadow_color));
    painter.setRenderHint(QPainter::Antialiasing, true);

    int shadow_radius = radius + border_thickness / 2 + shadow_blur_radius;
    QRectF shadow_rect(center.x() - shadow_radius + shadow_x,
                       center.y() - shadow_radius + shadow_y,
                       shadow_radius * 2,
                       shadow_radius * 2);

    QPainterPath shadow_path;
    shadow_path.addEllipse(shadow_rect);
    painter.fillPath(shadow_path, QBrush(gradient));
  }

  // Draws the circle with the border
  QPen pen(border_color, border_thickness);
  painter.setPen(pen);
  painter.setBrush(QBrush(fill_color));

  // Aligns the different circles drawn
  painter.drawEllipse(center.x() - radius, center.y() - radius, radius * 2, radius * 2);
}

// IF openpilot is not engaged DAD properties
AlertProperties DriverAlertDial::getAlertPropertiesForDisengaged() const {
  AlertProperties properties;

  // Fill color
  properties.outerColor = QColor(21, 21, 21, 100);
  properties.middleColor = QColor(21, 21, 21, 100);
  properties.innerColor = QColor(21, 21, 21, 100);

  // Border color
  properties.outerBorderColor = QColor(255, 0, 0, 50);
  properties.middleBorderColor = QColor(255, 245, 0, 50);
  properties.innerBorderColor = QColor(0, 209, 255, 50);

  // Shadow color
  properties.outerShadowColor = QColor(0, 0, 0, 0);
  properties.middleShadowColor = QColor(0, 0, 0, 0);
  properties.innerShadowColor = QColor(0, 0, 0, 0);

  // Shadow blur radius
  properties.outerShadowBlurRadius = 0;
  properties.middleShadowBlurRadius = 0;
  properties.innerShadowBlurRadius = 0;

  // Shadow opacity
  properties.outerShadowOpacity = 0;
  properties.middleShadowOpacity = 0;
  properties.innerShadowOpacity = 0;

  // Border thickness
  properties.outerBorderThickness = 20;
  properties.middleBorderThickness = 10;
  properties.innerBorderThickness = 10;

  properties.shadowX = 0;
  properties.shadowY = 0;

  // Alert ball fill color
  properties.alertBallOuterColor = QColor(0, 0, 0, 0);
  properties.alertBallInnerColor = QColor(0, 0, 0, 0);

  // Alert ball border color
  properties.alertBallOuterBorderColor = QColor(0, 0, 0, 0);
  properties.alertBallInnerBorderColor = QColor(0, 0, 0, 0);

  // Alert ball border thickness
  properties.alertBallOuterBorderThickness = 0;
  properties.alertBallInnerBorderThickness = 0;

  return properties;
}

// Returns the properties for the given confidence level
AlertProperties DriverAlertDial::getAlertProperties(cereal::ModelDataV2::ConfidenceClass conf) const {
  AlertProperties properties;
  switch (conf) {
    case cereal::ModelDataV2::ConfidenceClass::GREEN: // Low Alert

      // Fill color
      properties.outerColor = QColor(21, 21, 21);
      properties.middleColor = QColor(21, 21, 21);
      properties.innerColor = QColor(16, 68, 79);

      // Border color
      properties.outerBorderColor = QColor(79, 16, 16);
      properties.middleBorderColor = QColor(79, 77, 16);
      properties.innerBorderColor = QColor(0, 209, 255);

      // Shadow color
      properties.outerShadowColor = QColor(0, 209, 255);
      properties.middleShadowColor = QColor(0, 0, 0);
      properties.innerShadowColor = QColor(0, 209, 255);

      // Shadow blur radius
      properties.outerShadowBlurRadius = 40;
      properties.middleShadowBlurRadius = 0;
      properties.innerShadowBlurRadius = 80;

      // Shadow opacity
      properties.outerShadowOpacity = 245;
      properties.middleShadowOpacity = 0;
      properties.innerShadowOpacity = 150;

      // Border thickness
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;

      properties.shadowX = 0;
      properties.shadowY = 0;

      // Alert ball fill color
      properties.alertBallOuterColor = QColor(233, 233, 233);
      properties.alertBallInnerColor = QColor(0, 209, 255);

      // Alert ball border color
      properties.alertBallOuterBorderColor = QColor(0, 0, 0);
      properties.alertBallInnerBorderColor = QColor(0, 0, 0);

      // Alert ball border thickness
      properties.alertBallOuterBorderThickness = 7;
      properties.alertBallInnerBorderThickness = 7;

      break;

    case cereal::ModelDataV2::ConfidenceClass::YELLOW: // Medium Alert

      // Fill color
      properties.outerColor = QColor(28, 27, 21);
      properties.middleColor = QColor(80, 77, 16);
      properties.innerColor = QColor(80, 77, 16);

      // Border color
      properties.outerBorderColor = QColor(79, 16, 16);
      properties.middleBorderColor = QColor(255, 245, 16);
      properties.innerBorderColor = QColor(60 , 102, 76);

      // Shadow color
      properties.outerShadowColor = QColor(255, 245, 0);
      properties.middleShadowColor = QColor(255, 245, 0);
      properties.innerShadowColor = QColor(0, 0, 0);

      // Shadow blur radius
      properties.outerShadowBlurRadius = 40;
      properties.middleShadowBlurRadius = 90;
      properties.innerShadowBlurRadius = 0;

      // Shadow opacity
      properties.outerShadowOpacity = 245;
      properties.middleShadowOpacity = 160;
      properties.innerShadowOpacity = 0;

      // Border thickness
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;

      properties.shadowX = 0;
      properties.shadowY = 0;

      // Alert ball fill color
      properties.alertBallOuterColor = QColor(233, 233, 233);
      properties.alertBallInnerColor = QColor(255, 245, 0);

      // Alert ball border color
      properties.alertBallOuterBorderColor = QColor(0, 0, 0);
      properties.alertBallInnerBorderColor = QColor(0, 0, 0);

      // Alert ball border thickness
      properties.alertBallOuterBorderThickness = 7;
      properties.alertBallInnerBorderThickness = 7;

      break;

    case cereal::ModelDataV2::ConfidenceClass::RED: // High Alert

      // Fill color
      properties.outerColor = QColor(13, 13, 13);
      properties.middleColor = QColor(13, 13, 13);
      properties.innerColor = QColor(13, 13, 13);

      // Border color
      properties.outerBorderColor = QColor(255, 0, 0);
      properties.middleBorderColor = QColor(30, 30, 30);
      properties.innerBorderColor = QColor(30, 30, 30);

      // Shadow color
      properties.outerShadowColor = QColor(255, 0, 0);
      properties.middleShadowColor = QColor(0, 0, 0);
      properties.innerShadowColor = QColor(0, 0, 0);

      // Shadow blur radius
      properties.outerShadowBlurRadius = 40;
      properties.middleShadowBlurRadius = 0;
      properties.innerShadowBlurRadius = 0;

      // Shadow opacity
      properties.outerShadowOpacity = 245;
      properties.middleShadowOpacity = 0;
      properties.innerShadowOpacity = 0;

      // Border thickness
      properties.outerBorderThickness = 20;
      properties.middleBorderThickness = 10;
      properties.innerBorderThickness = 10;

      properties.shadowX = 0;
      properties.shadowY = 0;

      // Alert ball fill color
      properties.alertBallOuterColor = QColor(255, 0, 0);
      properties.alertBallInnerColor = QColor(255, 0, 0);

      // Alert ball border color
      properties.alertBallOuterBorderColor = QColor(127, 0, 0);
      properties.alertBallInnerBorderColor = QColor(0, 0, 0, 0);

      // Alert ball border thickness
      properties.alertBallOuterBorderThickness = 10;
      properties.alertBallInnerBorderThickness = 0;

      break;
  }
  return properties;

}

// ---
// MODIFY - This should calculate the position of the alert ball
// ---
QRectF DriverAlertDial::getZoneForConfidence(cereal::ModelDataV2::ConfidenceClass conf) const {
  QPointF center(width() / 2, height() / 2);

  switch (conf) {
    case cereal::ModelDataV2::ConfidenceClass::GREEN:
      return QRectF(center.x() - INNER_RADIUS, center.y() - INNER_RADIUS, INNER_RADIUS * 2, INNER_RADIUS * 2);
    case cereal::ModelDataV2::ConfidenceClass::YELLOW:
      return QRectF(center.x() - MIDDLE_RADIUS, center.y() - MIDDLE_RADIUS, MIDDLE_RADIUS * 2, MIDDLE_RADIUS * 2);
    case cereal::ModelDataV2::ConfidenceClass::RED:
      return QRectF(center.x() - OUTER_RADIUS, center.y() - OUTER_RADIUS, OUTER_RADIUS * 2, OUTER_RADIUS * 2);
  }
}

QPointF DriverAlertDial::calculateAlertBallPosition() const {
  float x = width() / 2;
  float y = height() / 2;

  QRectF currentZone = getZoneForConfidence(confidence);

  if (confidence != cereal::ModelDataV2::ConfidenceClass::GREEN) {
    if (acceleration > TOO_FAST_THRESHOLD) {
      y -= (acceleration - TOO_FAST_THRESHOLD) * BALL_MOVEMENT_SCALE;
    } else if (acceleration < TOO_SLOW_THRESHOLD) {
      y += (TOO_FAST_THRESHOLD - acceleration) * BALL_MOVEMENT_SCALE;
    }

    if (steering_torque < LOW_TORQUE_THRESHOLD) {
      x -= (LOW_TORQUE_THRESHOLD - steering_torque) * BALL_MOVEMENT_SCALE;
    } else if (steering_torque > HIGH_TORQUE_THRESHOLD) {
      x += (steering_torque - HIGH_TORQUE_THRESHOLD) * BALL_MOVEMENT_SCALE;
    }
  }

  // Clamp position within the current circular zone
  float radius = currentZone.width() / 2.0;
  float dist = std::sqrt((x - width() / 2) * (x - width() / 2) + (y - height() / 2) * (y - height() / 2));
  if (dist > radius) {
    float angle = std::atan2(y - height() / 2, x - width() / 2);
    x = width() / 2 + radius * std::cos(angle);
    y = height() / 2 + radius * std::sin(angle);
  }

  return QPointF(x, y);
}


// Paints the widget
void DriverAlertDial::paintEvent(QPaintEvent *event) {
  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);

  // Get properties based on engagement status and confidence level
  AlertProperties properties;
  if (is_engaged) {
    properties = getAlertProperties(confidence);
  } else {
    properties = getAlertPropertiesForDisengaged();
  }

  QPointF center(width() / 2, height() / 2);

  // Draws the outer
  drawCircle(painter,
            QPointF(width() / 2, height() / 2),
            properties.outerColor,
            properties.outerBorderColor,
            properties.outerShadowColor,
            properties.outerBorderThickness,
            155, // If changed, adjust OUTER_RADIUS in .h file
            properties.outerShadowBlurRadius,
            properties.outerShadowOpacity,
            properties.shadowX,
            properties.shadowY);

  // Draws the middle circle
  drawCircle(painter,
            QPointF(width() / 2, height() / 2),
            properties.middleColor,
            properties.middleBorderColor,
            properties.middleShadowColor,
            properties.middleBorderThickness,
            100, // If changed, adjust MIDDLE_RADIUS in .h file
            properties.middleShadowBlurRadius / 4,
            properties.middleShadowOpacity,
            properties.shadowX,
            properties.shadowY);

  // Draws the inner circle
  drawCircle(painter,
            QPointF(width() / 2, height() / 2),
            properties.innerColor,
            properties.innerBorderColor,
            properties.innerShadowColor,
            properties.innerBorderThickness,
            35, // If changed, adjust INNER_RADIUS in .h file
            properties.innerShadowBlurRadius / 4,
            properties.innerShadowOpacity,
            properties.shadowX,
            properties.shadowY);


  QPointF ball_pos = calculateAlertBallPosition();

  // Draws outer part of alert ball
  painter.setBrush(properties.alertBallOuterColor);
  painter.setPen(QPen(properties.alertBallOuterBorderColor, properties.alertBallOuterBorderThickness));
  painter.drawEllipse(ball_pos.x() - BALL_OUTER_RADIUS, ball_pos.y() - BALL_OUTER_RADIUS, BALL_OUTER_RADIUS * 2, BALL_OUTER_RADIUS * 2);

  // Draws the inner part of alert ball
  painter.setBrush(properties.alertBallInnerColor);
  painter.setPen(QPen(properties.alertBallInnerBorderColor, properties.alertBallInnerBorderThickness));
  painter.drawEllipse(ball_pos.x() - BALL_INNER_RADIUS, ball_pos.y() - BALL_INNER_RADIUS, BALL_INNER_RADIUS * 2, BALL_INNER_RADIUS * 2);

  // // REMOVE SOON ---
  // // Box around UI to see sizing easier REMOVE
  // QPen borderPen(Qt::blue, 2);
  // painter.setPen(borderPen);
  // painter.setBrush(Qt::NoBrush);

  // // Draw a rectangle around the widget
  // QRectF borderRect(0, 0, width(), height());
  // painter.drawRect(borderRect);
  // // ---


  // Restore painter state
  painter.restore();
}